"""PostgreSQL owner for local attachment bytes, metadata and scan state."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from application.media_asset import (
    AssetAdmission,
    AssetLifecycle,
    AssetModality,
    AssetStatus,
    MediaAssetError,
    ScanOutcome,
)


class PostgresMediaAssetStore:
    backend = {
        "mode": "postgres",
        "location": "dialogpilot_app.media_assets",
    }

    def __init__(self, pool):
        self.pool = pool

    def put(self, admission: AssetAdmission, content: bytes) -> AssetAdmission:
        if len(content) != admission.byte_size:
            raise MediaAssetError("INVALID_ASSET", "asset byte size changed")
        with self.pool.transaction() as connection:
            row = connection.execute("""
                INSERT INTO dialogpilot_app.media_assets (
                    asset_id,tenant_id,user_id,filename,modality,media_type,
                    byte_size,checksum,content,status,schema_version
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (asset_id) DO UPDATE SET asset_id=EXCLUDED.asset_id
                WHERE dialogpilot_app.media_assets.tenant_id=EXCLUDED.tenant_id
                  AND dialogpilot_app.media_assets.user_id=EXCLUDED.user_id
                  AND dialogpilot_app.media_assets.checksum=EXCLUDED.checksum
                  AND dialogpilot_app.media_assets.content=EXCLUDED.content
                RETURNING status
            """, (
                admission.asset_id, admission.tenant_id, admission.user_id,
                admission.filename, admission.modality.value,
                admission.media_type, admission.byte_size, admission.checksum,
                content, admission.status.value, admission.schema_version,
            )).fetchone()
            if row is None:
                raise MediaAssetError("ASSET_IDENTITY_CONFLICT", "asset identity changed")
            connection.execute("""
                INSERT INTO dialogpilot_app.media_asset_turn_bindings (
                    asset_id,tenant_id,user_id,turn_key
                ) VALUES (%s,%s,%s,%s)
                ON CONFLICT (asset_id,turn_key) DO NOTHING
            """, (
                admission.asset_id, admission.tenant_id,
                admission.user_id, admission.turn_key,
            ))
        return replace(admission, status=AssetStatus(row[0]))

    def transition(
        self,
        asset_id: str,
        *,
        tenant_id: str,
        user_id: str,
        current: AssetStatus,
        target: AssetStatus,
    ) -> AssetStatus:
        AssetLifecycle.transition(current, target)
        with self.pool.transaction() as connection:
            row = connection.execute("""
                UPDATE dialogpilot_app.media_assets
                SET status=%s,updated_at=transaction_timestamp()
                WHERE asset_id=%s AND tenant_id=%s AND user_id=%s AND status=%s
                RETURNING status
            """, (
                target.value, asset_id, tenant_id, user_id, current.value,
            )).fetchone()
            if row is None:
                observed = connection.execute("""
                    SELECT status FROM dialogpilot_app.media_assets
                    WHERE asset_id=%s AND tenant_id=%s AND user_id=%s
                """, (asset_id, tenant_id, user_id)).fetchone()
                if observed is None:
                    raise MediaAssetError("ASSET_NOT_FOUND", "asset is unavailable")
                if observed[0] == target.value:
                    return target
                raise MediaAssetError("ASSET_VERSION_CONFLICT", "asset status changed")
        return target

    def get(
        self, asset_id: str, *, tenant_id: str, user_id: str,
    ) -> tuple[AssetAdmission, bytes]:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT asset.asset_id,asset.tenant_id,asset.user_id,
                       binding.turn_key,asset.filename,asset.modality,
                       asset.media_type,asset.byte_size,asset.checksum,
                       asset.status,asset.schema_version,asset.content
                FROM dialogpilot_app.media_assets asset
                JOIN LATERAL (
                    SELECT turn_key FROM dialogpilot_app.media_asset_turn_bindings
                    WHERE asset_id=asset.asset_id ORDER BY created_at,turn_key LIMIT 1
                ) binding ON true
                WHERE asset.asset_id=%s AND asset.tenant_id=%s AND asset.user_id=%s
            """, (asset_id, tenant_id, user_id)).fetchone()
        if row is None:
            raise MediaAssetError("ASSET_NOT_FOUND", "asset is unavailable")
        return AssetAdmission(
            asset_id=row[0], tenant_id=row[1], user_id=row[2], turn_key=row[3],
            filename=row[4], modality=AssetModality(row[5]), media_type=row[6],
            byte_size=int(row[7]), checksum=row[8], status=AssetStatus(row[9]),
            schema_version=row[10],
        ), bytes(row[11])

    def is_bound(
        self, asset_id: str, *, tenant_id: str, user_id: str, turn_key: str,
    ) -> bool:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT 1 FROM dialogpilot_app.media_asset_turn_bindings
                WHERE asset_id=%s AND tenant_id=%s AND user_id=%s AND turn_key=%s
            """, (asset_id, tenant_id, user_id, turn_key)).fetchone()
        return row is not None

    def delete_subject(self, *, tenant_id: str, user_id: str) -> int:
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                DELETE FROM dialogpilot_app.media_assets
                WHERE tenant_id=%s AND user_id=%s RETURNING asset_id
            """, (tenant_id, user_id)).fetchall()
        return len(rows)


class MediaAssetService:
    """Persist first, then scan; scan completion never implies OCR/VLM work."""

    def __init__(
        self,
        store: PostgresMediaAssetStore,
        scanner: Callable[[bytes], ScanOutcome],
    ):
        self.store = store
        self.scanner = scanner

    def admit_and_scan(
        self, admission: AssetAdmission, content: bytes,
    ) -> AssetAdmission:
        stored = self.store.put(admission, content)
        if stored.status in {
            AssetStatus.SCANNED, AssetStatus.FAILED, AssetStatus.QUARANTINED,
        }:
            return stored
        self.store.transition(
            stored.asset_id, tenant_id=stored.tenant_id, user_id=stored.user_id,
            current=AssetStatus.UPLOADED, target=AssetStatus.SCANNING,
        )
        try:
            outcome = ScanOutcome(self.scanner(content))
        except Exception:
            outcome = ScanOutcome.FAILED
        target = AssetLifecycle.scan_target(outcome)
        self.store.transition(
            stored.asset_id, tenant_id=stored.tenant_id, user_id=stored.user_id,
            current=AssetStatus.SCANNING, target=target,
        )
        return replace(stored, status=target)


def local_signature_scan(content: bytes) -> ScanOutcome:
    """Deterministic local demo scanner; production scanners remain an adapter."""
    return (
        ScanOutcome.MALICIOUS
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in content
        else ScanOutcome.CLEAN
    )
