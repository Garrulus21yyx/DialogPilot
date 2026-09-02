"""M1-T03A writer fence, binding CAS and crash convergence tests."""
import pytest

from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory
from infrastructure.delivery_binding import (
    DeliveryBindingConflict,
    DeliveryRepositoryState,
    PostgresDeliveryBindingRepository,
    ResponseDeliveryCutoverCoordinator,
)
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.response_delivery_cutover import (
    LegacyResponseDeliveryExporter,
    PostgresResponseDeliveryBackfill,
)
from services.response_delivery import (
    DeliveryStatus,
    ResponseDeliveryService,
    ResponseDeliveryWritesFrozen,
)


CREATED = "2026-09-02T07:00:00+00:00"
SWITCHED = "2026-09-02T07:30:00+00:00"


@pytest.fixture()
def binding_pool(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=4,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.publication_revision_events,
                dialogpilot_app.delivery_receipts,
                dialogpilot_app.delivery_outbox,
                dialogpilot_app.resume_requested_outbox,
                dialogpilot_app.workflow_start_outbox,
                dialogpilot_app.response_deliveries,
                dialogpilot_app.workflow_invocations,
                dialogpilot_app.conversation_events,
                dialogpilot_app.conversation_turns,
                dialogpilot_app.conversations
            CASCADE
        """)
        connection.execute(
            "DELETE FROM dialogpilot_platform.delivery_binding_events"
        )
        connection.execute("""
            UPDATE dialogpilot_platform.delivery_repository_binding
            SET state='SQLITE_ACTIVE', generation=1, freeze_id=NULL,
                snapshot_sha256=NULL, switched_at=NULL,
                updated_at=transaction_timestamp()
            WHERE singleton=TRUE
        """)
    try:
        yield pool
    finally:
        pool.close()


def _identity(suffix, conversation="binding-conversation"):
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-binding",
        user_id="user-binding",
        conversation_id=conversation,
        request_id=f"request-{suffix}",
    )


def _seed(pool, identity):
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity=identity,
        message="question",
        pinned_versions={"bundle": "legacy", "runtime": "compat-v1"},
        created_at=CREATED,
    ))


def _select(legacy, identity, text="answer"):
    return legacy.select_response(
        user_id=str(identity.user_id),
        conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id),
        response_text=text,
        identity_metadata=identity.metadata(),
    )


def _coordinator(pool, path, *, fault_hook=None):
    legacy = ResponseDeliveryService(str(path))
    return legacy, ResponseDeliveryCutoverCoordinator(
        legacy=legacy,
        legacy_database_path=str(path),
        binding=PostgresDeliveryBindingRepository(pool),
        backfill=PostgresResponseDeliveryBackfill(pool),
        fault_hook=fault_hook,
    )


def test_legacy_fence_is_in_same_transaction_as_selection_and_ack(tmp_path):
    path = tmp_path / "legacy.db"
    identity = _identity("fence", conversation="fence-conversation")
    legacy = ResponseDeliveryService(str(path))
    selected = _select(legacy, identity)
    legacy.freeze_writes("freeze-fence")

    with pytest.raises(ResponseDeliveryWritesFrozen, match="frozen"):
        _select(legacy, _identity("blocked", conversation="fence-conversation"))
    with pytest.raises(ResponseDeliveryWritesFrozen, match="frozen"):
        legacy.acknowledge(
            selected.response_id,
            user_id=str(identity.user_id),
            status=DeliveryStatus.READ,
        )
    assert legacy.list_after(
        user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
    )[0].status is DeliveryStatus.SELECTED

    legacy.unfreeze_before_cutover("freeze-fence")
    assert legacy.acknowledge(
        selected.response_id,
        user_id=str(identity.user_id),
        status=DeliveryStatus.READ,
    ).status is DeliveryStatus.READ
    legacy.freeze_writes("freeze-retire")
    legacy.retire_writes("freeze-retire")
    with pytest.raises(ResponseDeliveryWritesFrozen, match="matching pre-cutover"):
        legacy.unfreeze_before_cutover("freeze-retire")


def test_final_delta_switches_binding_then_irreversibly_retires_sqlite(
    binding_pool, tmp_path,
):
    path = tmp_path / "legacy.db"
    legacy, coordinator = _coordinator(binding_pool, path)
    first_identity = _identity("first")
    second_identity = _identity("delta")
    for identity in (first_identity, second_identity):
        _seed(binding_pool, identity)
    _select(legacy, first_identity, "first answer")
    initial = LegacyResponseDeliveryExporter().export(str(path))
    PostgresResponseDeliveryBackfill(binding_pool).apply(initial)
    _select(legacy, second_identity, "delta answer")

    frozen = coordinator.prepare_freeze(
        freeze_id="freeze-final", actor="operator",
    )
    assert frozen.state is DeliveryRepositoryState.FROZEN
    result = coordinator.finalize_and_activate(
        freeze_id="freeze-final", actor="operator", switched_at=SWITCHED,
    )

    assert result.binding.state is DeliveryRepositoryState.POSTGRES_ACTIVE
    assert result.reconciliation.matched is True
    assert result.reconciliation.target_count == 2
    assert result.legacy_writer_state == "RETIRED"
    assert coordinator.assert_worker_resume_safe() == result.binding
    with pytest.raises(ResponseDeliveryWritesFrozen, match="retired"):
        _select(legacy, _identity("after-switch"))
    with pytest.raises(DeliveryBindingConflict, match="cannot roll back"):
        coordinator.abort_before_switch(
            freeze_id="freeze-final", actor="operator", reason="too late",
        )


@pytest.mark.parametrize("failure_stage", [
    "after_legacy_freeze",
    "after_binding_freeze",
    "after_final_export",
    "after_final_backfill",
    "after_binding_switch",
    "after_legacy_retire",
])
def test_every_cutover_crash_point_converges_without_two_writers(
    binding_pool, tmp_path, failure_stage,
):
    path = tmp_path / f"{failure_stage}.db"
    identity = _identity(failure_stage, conversation=f"conv-{failure_stage}")
    _seed(binding_pool, identity)
    legacy = ResponseDeliveryService(str(path))
    _select(legacy, identity)
    injected = {"done": False}

    def fail_once(stage):
        if stage == failure_stage and not injected["done"]:
            injected["done"] = True
            raise RuntimeError("injected cutover crash")

    _, crashing = _coordinator(binding_pool, path, fault_hook=fail_once)
    try:
        if failure_stage in {"after_legacy_freeze", "after_binding_freeze"}:
            crashing.prepare_freeze(freeze_id="freeze-crash", actor="operator")
        else:
            crashing.prepare_freeze(freeze_id="freeze-crash", actor="operator")
            crashing.finalize_and_activate(
                freeze_id="freeze-crash", actor="operator", switched_at=SWITCHED,
            )
    except RuntimeError as exc:
        assert str(exc) == "injected cutover crash"
    else:
        raise AssertionError("fault hook did not execute")

    recovered_legacy, recovered = _coordinator(binding_pool, path)
    binding = PostgresDeliveryBindingRepository(binding_pool).get()
    legacy_state = recovered_legacy.writer_state()[0]
    assert not (
        binding.state is DeliveryRepositoryState.POSTGRES_ACTIVE
        and legacy_state == "ACTIVE"
    )
    if binding.state is not DeliveryRepositoryState.POSTGRES_ACTIVE:
        recovered.prepare_freeze(freeze_id="freeze-crash", actor="recovery")
    result = recovered.finalize_and_activate(
        freeze_id="freeze-crash", actor="recovery", switched_at=SWITCHED,
    )
    assert result.binding.state is DeliveryRepositoryState.POSTGRES_ACTIVE
    assert recovered_legacy.writer_state()[0] == "RETIRED"
    assert result.reconciliation.matched is True


def test_abort_is_only_available_before_binding_switch(binding_pool, tmp_path):
    path = tmp_path / "abort.db"
    legacy, coordinator = _coordinator(binding_pool, path)
    identity = _identity("abort", conversation="abort-conversation")
    _select(legacy, identity)
    coordinator.prepare_freeze(freeze_id="freeze-abort", actor="operator")
    binding = coordinator.abort_before_switch(
        freeze_id="freeze-abort", actor="operator", reason="reconcile failed",
    )
    assert binding.state is DeliveryRepositoryState.SQLITE_ACTIVE
    assert legacy.writer_state()[0] == "ACTIVE"
    assert _select(
        legacy, _identity("after-abort", conversation="abort-conversation"),
    ).status is DeliveryStatus.SELECTED
