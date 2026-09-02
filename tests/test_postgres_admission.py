"""M1-T02 inbound-first transaction, outbox lease and stable dispatch tests."""
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import multiprocessing

import pytest

from application.admission_contract import (
    AdmissionConflict,
    AdmissionCreated,
    AdmissionExisting,
    AdmissionStatus,
    ClaimStart,
    ExecutionPointer,
)
from application.inbound_admission import (
    ExplicitResumeBinding,
    InvalidResumeInbound,
    NewInvocationInbound,
    ResumeQueued,
    ResumeRejected,
    ResumeRejectionCode,
    ResumeTarget,
    ValidResumeInbound,
)
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import (
    PostgresAdmissionUnitOfWork,
    PostgresStartOutbox,
    StartOutboxDispatcher,
)
from infrastructure.postgres_conversation import PostgresInvocationRepository


@pytest.fixture()
def admission_components(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=6,
    ))
    pool.open()
    _reset(pool)
    try:
        yield pool, PostgresAdmissionUnitOfWork(pool), PostgresStartOutbox(pool)
    finally:
        pool.close()


def _identity(suffix, conversation="admission-conversation"):
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-admission",
        user_id="user-admission",
        conversation_id=conversation,
        request_id=f"request-{suffix}",
    )


def _new(identity, message="hello"):
    return NewInvocationInbound(
        identity=identity,
        message=message,
        pinned_versions={"bundle": "bundle-v1", "runtime": "compat-v1"},
        created_at="2026-09-02T04:00:00+02:00",
    )


def _counts(pool):
    with pool.transaction() as connection:
        return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in (
                         "dialogpilot_app.conversation_turns",
                         "dialogpilot_app.conversation_events",
                         "dialogpilot_app.workflow_invocations",
                         "dialogpilot_app.workflow_start_outbox",
                         "dialogpilot_app.resume_requested_outbox",
                     ))


def _reset(pool):
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.resume_requested_outbox,
                dialogpilot_app.workflow_start_outbox,
                dialogpilot_app.response_deliveries,
                dialogpilot_app.workflow_invocations,
                dialogpilot_app.conversation_events,
                dialogpilot_app.conversation_turns,
                dialogpilot_app.conversations
            CASCADE
        """)


def _multiprocess_admit(database_url: str) -> str:
    pool = PostgresPool(PostgresPoolConfig(database_url, min_size=0, max_size=1))
    pool.open()
    try:
        result = PostgresAdmissionUnitOfWork(pool).admit_new(
            _new(_identity("multiprocess", conversation="conv-multiprocess"))
        )
        return type(result).__name__
    finally:
        pool.close()


def test_new_inbound_commits_turn_event_invocation_and_start_once(
    admission_components,
):
    pool, admission, _ = admission_components
    identity = _identity("new")
    assert isinstance(admission.admit_new(_new(identity)), AdmissionCreated)
    assert isinstance(admission.admit_new(_new(identity)), AdmissionExisting)
    assert isinstance(admission.admit_new(_new(identity, "changed")), AdmissionConflict)
    assert _counts(pool) == (1, 1, 1, 1, 0)


@pytest.mark.parametrize(
    "failure_stage",
    ["after_inbound", "after_accepted_event", "after_invocation", "after_start_outbox"],
)
def test_crash_at_any_admission_stage_rolls_back_every_fact(
    postgres_database_url, failure_stage,
):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=2))
    pool.open()
    _reset(pool)
    identity = _identity(f"crash-{failure_stage}", conversation=f"conv-{failure_stage}")

    def fail(stage):
        if stage == failure_stage:
            raise RuntimeError("injected crash")

    try:
        admission = PostgresAdmissionUnitOfWork(pool, fault_hook=fail)
        with pytest.raises(RuntimeError, match="injected crash"):
            admission.admit_new(_new(identity))
        assert _counts(pool) == (0, 0, 0, 0, 0)
        assert isinstance(
            PostgresAdmissionUnitOfWork(pool).admit_new(_new(identity)),
            AdmissionCreated,
        )
    finally:
        pool.close()


def test_start_outbox_lease_expiry_reclaims_same_item_not_new_identity(
    admission_components,
):
    _, admission, outbox = admission_components
    identity = _identity("lease", conversation="conv-lease")
    admission.admit_new(_new(identity))
    worker_one = outbox.claim(ClaimStart(
        "worker-1", "2026-09-02T04:00:00+02:00",
        "2026-09-02T04:01:00+02:00", 1,
    ))
    assert len(worker_one) == 1
    assert outbox.claim(ClaimStart(
        "worker-2", "2026-09-02T04:00:30+02:00",
        "2026-09-02T04:01:30+02:00", 1,
    )) == ()
    worker_two = outbox.claim(ClaimStart(
        "worker-2", "2026-09-02T04:01:01+02:00",
        "2026-09-02T04:02:01+02:00", 1,
    ))
    assert worker_two[0].outbox_id == worker_one[0].outbox_id
    assert worker_two[0].invocation_key == identity.invocation_key
    assert worker_two[0].attempt == 2


def test_start_outbox_attempt_fences_late_worker_even_when_name_is_reused(
    admission_components,
):
    _, admission, outbox = admission_components
    identity = _identity("fence", conversation="conv-fence")
    admission.admit_new(_new(identity))
    stale = outbox.claim(ClaimStart(
        "worker-stable-name", "2026-09-02T04:00:00+02:00",
        "2026-09-02T04:01:00+02:00", 1,
    ))[0]
    current = outbox.claim(ClaimStart(
        "worker-stable-name", "2026-09-02T04:01:01+02:00",
        "2026-09-02T04:02:01+02:00", 1,
    ))[0]

    assert current.attempt == stale.attempt + 1
    assert outbox.acknowledge(
        stale.outbox_id, worker_id="worker-stable-name", attempt=stale.attempt,
    ) is False
    assert outbox.renew(
        stale.outbox_id, worker_id="worker-stable-name", attempt=stale.attempt,
        now="2026-09-02T04:01:02+02:00",
        lease_until="2026-09-02T04:03:00+02:00",
    ) is False
    assert outbox.acknowledge(
        current.outbox_id, worker_id="worker-stable-name", attempt=current.attempt,
    ) is True


def test_multiprocess_same_invocation_has_one_primary_writer(
    postgres_database_url, admission_components,
):
    pool, _, _ = admission_components
    with ProcessPoolExecutor(
        max_workers=8, mp_context=multiprocessing.get_context("spawn"),
    ) as executor:
        outcomes = list(executor.map(
            _multiprocess_admit, [postgres_database_url] * 16,
        ))

    assert outcomes.count("AdmissionCreated") == 1
    assert outcomes.count("AdmissionExisting") == 15
    assert _counts(pool) == (1, 1, 1, 1, 0)


def test_dispatch_retry_binds_same_run_and_acknowledges_same_outbox(
    admission_components,
):
    pool, admission, outbox = admission_components
    identity = _identity("dispatch", conversation="conv-dispatch")
    admission.admit_new(_new(identity))

    class StableBinder:
        def __init__(self):
            self.run_ids = {}

        def get_or_create(self, *, invocation_key, workflow_run_id, pinned_versions):
            self.run_ids.setdefault(str(invocation_key), str(workflow_run_id))
            return ExecutionPointer("compat", "v1", self.run_ids[str(invocation_key)])

    binder = StableBinder()
    dispatcher = StartOutboxDispatcher(
        outbox, PostgresInvocationRepository(pool), binder,
    )
    attempts = dispatcher.dispatch_once(ClaimStart(
        "worker", "2026-09-02T04:00:00+02:00",
        "2026-09-02T04:01:00+02:00", 1,
    ))
    assert attempts[0].status == "bound"
    record = PostgresInvocationRepository(pool).get(
        identity.invocation_key,
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
    )
    assert record.status is AdmissionStatus.EXECUTION_BOUND
    assert record.execution_pointer.run_id == str(identity.workflow_run_id)
    assert dispatcher.dispatch_once(ClaimStart(
        "worker", "2026-09-02T04:02:00+02:00",
        "2026-09-02T04:03:00+02:00", 1,
    )) == ()


def test_crash_after_run_or_cas_reuses_same_run_and_finishes_same_outbox(
    admission_components,
):
    pool, admission, outbox = admission_components
    identity = _identity("dispatch-crash", conversation="conv-dispatch-crash")
    admission.admit_new(_new(identity))

    class StableBinder:
        def __init__(self):
            self.created = 0
            self.run_id = ""

        def get_or_create(self, *, invocation_key, workflow_run_id, pinned_versions):
            if not self.run_id:
                self.created += 1
                self.run_id = str(workflow_run_id)
            return ExecutionPointer("compat", "v1", self.run_id)

    binder = StableBinder()
    injected = {"done": False}

    def fail_once(stage):
        if stage == "after_admission_cas" and not injected["done"]:
            injected["done"] = True
            raise RuntimeError("crash after CAS")

    dispatcher = StartOutboxDispatcher(
        outbox, PostgresInvocationRepository(pool), binder, fault_hook=fail_once,
    )
    first = dispatcher.dispatch_once(ClaimStart(
        "worker-1", "2026-09-02T04:00:00+02:00",
        "2026-09-02T04:01:00+02:00", 1,
    ))
    assert first[0].status == "retry"
    second = dispatcher.dispatch_once(ClaimStart(
        "worker-2", "2026-09-02T04:01:00+02:00",
        "2026-09-02T04:02:00+02:00", 1,
    ))
    assert second[0].status == "bound"
    assert second[0].run_id == str(identity.workflow_run_id)
    assert binder.created == 1


def test_crash_after_outbox_ack_is_known_bound_not_requeued(admission_components):
    pool, admission, outbox = admission_components
    identity = _identity("dispatch-acked", conversation="conv-dispatch-acked")
    admission.admit_new(_new(identity))

    class Binder:
        def get_or_create(self, *, invocation_key, workflow_run_id, pinned_versions):
            return ExecutionPointer("compat", "v1", str(workflow_run_id))

    def fail_after_ack(stage):
        if stage == "after_outbox_ack":
            raise RuntimeError("process exit after durable ACK")

    dispatcher = StartOutboxDispatcher(
        outbox, PostgresInvocationRepository(pool), Binder(),
        fault_hook=fail_after_ack,
    )
    result = dispatcher.dispatch_once(ClaimStart(
        "worker", "2026-09-02T04:00:00+02:00",
        "2026-09-02T04:01:00+02:00", 1,
    ))
    assert result[0].status == "bound"
    assert result[0].error_type == "RuntimeError"
    assert dispatcher.dispatch_once(ClaimStart(
        "worker-2", "2026-09-02T04:02:00+02:00",
        "2026-09-02T04:03:00+02:00", 1,
    )) == ()


def _binding(signal="signal-1"):
    return ExplicitResumeBinding(signal, 3, "approval", "publication-1", "a" * 64)


def test_valid_resume_writes_only_inbound_event_and_resume_outbox(
    admission_components,
):
    pool, admission, _ = admission_components
    identity = _identity("resume", conversation="conv-resume")
    binding = _binding()
    target = ResumeTarget(
        binding.signal_id, binding.expected_version, "existing-run",
        binding.kind, binding.schema_fingerprint,
    )
    command = ValidResumeInbound(
        identity, "approve", binding, target, "2026-09-02T04:00:00+02:00",
    )
    first = admission.admit_resume(command)
    second = admission.admit_resume(command)
    assert isinstance(first, ResumeQueued)
    assert second == first
    assert _counts(pool) == (1, 1, 0, 0, 1)


def test_invalid_explicit_resume_writes_rejection_but_no_start_or_resume(
    admission_components,
):
    pool, admission, _ = admission_components
    identity = _identity("resume-rejected", conversation="conv-resume-rejected")
    result = admission.admit_resume(InvalidResumeInbound(
        identity, "approve", _binding("forbidden-signal"),
        ResumeRejectionCode.UNAUTHORIZED, "2026-09-02T04:00:00+02:00",
    ))
    assert isinstance(result, ResumeRejected)
    assert result.code is ResumeRejectionCode.UNAUTHORIZED
    assert _counts(pool) == (1, 1, 0, 0, 0)


def test_changed_resume_binding_on_same_request_is_conflict(
    admission_components,
):
    pool, admission, _ = admission_components
    identity = _identity("resume-conflict", conversation="conv-resume-conflict")
    binding = _binding("signal-original")
    target = ResumeTarget(
        binding.signal_id, binding.expected_version, "run",
        binding.kind, binding.schema_fingerprint,
    )
    admission.admit_resume(ValidResumeInbound(
        identity, "approve", binding, target, "2026-09-02T04:00:00+02:00",
    ))
    changed = replace(binding, signal_id="signal-changed")
    result = admission.admit_resume(ValidResumeInbound(
        identity, "approve", changed,
        replace(target, signal_id="signal-changed"),
        "2026-09-02T04:00:00+02:00",
    ))
    assert isinstance(result, ResumeRejected)
    assert result.code is ResumeRejectionCode.IDEMPOTENCY_CONFLICT
    assert _counts(pool) == (1, 1, 0, 0, 1)
