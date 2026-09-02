"""PostgreSQL attachment ownership, idempotency and scan-state tests."""
import pytest

from application.media_asset import (
    AssetAdmissionPolicy,
    AssetStatus,
    MediaAssetError,
)
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_media_asset_store import (
    MediaAssetService,
    PostgresMediaAssetStore,
    local_signature_scan,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"


@pytest.fixture()
def media_store(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("TRUNCATE dialogpilot_app.media_assets CASCADE")
    try:
        yield PostgresMediaAssetStore(pool)
    finally:
        pool.close()


def _admission(*, user_id="user-a", turn_key="turn-a", content=PNG):
    return AssetAdmissionPolicy().admit(
        tenant_id="tenant-a", user_id=user_id, turn_key=turn_key,
        filename="screen.png", declared_media_type="image/png", content=content,
    )


def test_put_scan_read_and_rebind_are_idempotent(media_store):
    service = MediaAssetService(media_store, local_signature_scan)
    first = service.admit_and_scan(_admission(), PNG)
    rebound = service.admit_and_scan(_admission(turn_key="turn-b"), PNG)
    loaded, content = media_store.get(
        first.asset_id, tenant_id="tenant-a", user_id="user-a",
    )
    assert first.status is AssetStatus.SCANNED
    assert rebound.asset_id == first.asset_id
    assert rebound.status is AssetStatus.SCANNED
    assert loaded.status is AssetStatus.SCANNED
    assert content == PNG
    assert media_store.is_bound(
        first.asset_id, tenant_id="tenant-a", user_id="user-a", turn_key="turn-b",
    )


def test_asset_read_is_user_scoped_and_subject_delete_cascades_bindings(media_store):
    stored = MediaAssetService(
        media_store, local_signature_scan,
    ).admit_and_scan(_admission(), PNG)
    with pytest.raises(MediaAssetError) as error:
        media_store.get(stored.asset_id, tenant_id="tenant-a", user_id="user-b")
    assert error.value.code == "ASSET_NOT_FOUND"
    assert media_store.delete_subject(tenant_id="tenant-a", user_id="user-a") == 1
    with pytest.raises(MediaAssetError):
        media_store.get(stored.asset_id, tenant_id="tenant-a", user_id="user-a")


def test_malicious_signature_is_quarantined_and_cannot_be_rescanned(media_store):
    content = PNG + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
    stored = MediaAssetService(
        media_store, local_signature_scan,
    ).admit_and_scan(_admission(content=content), content)
    assert stored.status is AssetStatus.QUARANTINED
    replay = MediaAssetService(
        media_store, lambda _content: (_ for _ in ()).throw(AssertionError()),
    ).admit_and_scan(_admission(content=content), content)
    assert replay.status is AssetStatus.QUARANTINED


def test_status_cas_rejects_stale_writer(media_store):
    admission = media_store.put(_admission(), PNG)
    media_store.transition(
        admission.asset_id, tenant_id="tenant-a", user_id="user-a",
        current=AssetStatus.UPLOADED, target=AssetStatus.SCANNING,
    )
    media_store.transition(
        admission.asset_id, tenant_id="tenant-a", user_id="user-a",
        current=AssetStatus.SCANNING, target=AssetStatus.SCANNED,
    )
    with pytest.raises(MediaAssetError) as error:
        media_store.transition(
            admission.asset_id, tenant_id="tenant-a", user_id="user-a",
            current=AssetStatus.SCANNING, target=AssetStatus.FAILED,
        )
    assert error.value.code == "ASSET_VERSION_CONFLICT"
