"""SQLite ResponseDelivery export, PostgreSQL backfill and shadow reconciliation.

The legacy database remains authoritative while these functions run.  They never
write SQLite and never infer missing tenant or invocation ownership.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from application.conversation_store import content_hash
from infrastructure.postgres import PostgresPool


class DeliveryCutoverError(RuntimeError):
    pass


class LegacyContractError(DeliveryCutoverError):
    pass


class TargetNotEmptyError(DeliveryCutoverError):
    pass


class ReconciliationError(DeliveryCutoverError):
    pass


class BackfillMode(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    FINAL_DELTA = "FINAL_DELTA"


LEGACY_STATUS_MAP = {
    "selected": "DELIVERY_UNCERTAIN",
    "delivered": "DELIVERED",
    "read": "READ",
}


@dataclass(frozen=True)
class LegacyDeliveryRow:
    response_id: str
    tenant_id: str
    user_id: str
    conversation_id: str
    request_id: str
    invocation_key: str
    legacy_seq: int
    response_text: str
    legacy_status: str
    target_status: str
    selected_at: str
    delivered_at: str | None
    read_at: str | None
    target_outbox_id: str
    row_sha256: str

    def comparison_record(self) -> dict[str, Any]:
        return {
            "publication_id": self.response_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "request_id": self.request_id,
            "invocation_key": self.invocation_key,
            "seq": self.legacy_seq,
            "response_text": self.response_text,
            "status": self.target_status,
            "selected_at": self.selected_at,
            "delivered_at": self.delivered_at,
            "read_at": self.read_at,
            "outbox_id": self.target_outbox_id,
        }


@dataclass(frozen=True)
class LegacyDeliverySnapshot:
    schema_version: str
    source_database: str
    exported_at: str
    rows: tuple[LegacyDeliveryRow, ...]
    row_count: int
    status_counts: Mapping[str, int]
    ids_sha256: str
    content_sha256: str

    def __post_init__(self) -> None:
        if self.row_count != len(self.rows):
            raise LegacyContractError("snapshot row_count does not match rows")
        if dict(self.status_counts) != _status_counts(
            row.target_status for row in self.rows
        ):
            raise LegacyContractError("snapshot status counts do not match rows")
        _require_unique(
            (row.response_id for row in self.rows), "legacy response_id",
        )
        _require_unique(
            (row.target_outbox_id for row in self.rows), "target outbox_id",
        )
        _require_unique(
            (row.invocation_key for row in self.rows), "final invocation_key",
        )
        _require_unique(
            (
                (row.tenant_id, row.user_id, row.conversation_id, row.legacy_seq)
                for row in self.rows
            ),
            "legacy conversation sequence",
        )
        for row in self.rows:
            expected_row_hash = content_hash({
                key: value
                for key, value in asdict(row).items()
                if key != "row_sha256"
            })
            if row.row_sha256 != expected_row_hash:
                raise LegacyContractError(
                    f"legacy row checksum mismatch: {row.response_id}"
                )
        if self.content_sha256 != _snapshot_content_hash(self.rows):
            raise LegacyContractError("snapshot content checksum mismatch")
        if self.ids_sha256 != _ids_hash(row.response_id for row in self.rows):
            raise LegacyContractError("snapshot ID checksum mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_database": self.source_database,
            "exported_at": self.exported_at,
            "rows": [asdict(row) for row in self.rows],
            "row_count": self.row_count,
            "status_counts": dict(self.status_counts),
            "ids_sha256": self.ids_sha256,
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LegacyDeliverySnapshot":
        return cls(
            schema_version=str(raw["schema_version"]),
            source_database=str(raw["source_database"]),
            exported_at=str(raw["exported_at"]),
            rows=tuple(LegacyDeliveryRow(**row) for row in raw["rows"]),
            row_count=int(raw["row_count"]),
            status_counts={
                str(key): int(value)
                for key, value in dict(raw["status_counts"]).items()
            },
            ids_sha256=str(raw["ids_sha256"]),
            content_sha256=str(raw["content_sha256"]),
        )


@dataclass(frozen=True)
class DeliveryReconciliationReport:
    schema_version: str
    snapshot_sha256: str
    source_count: int
    target_count: int
    source_status_counts: Mapping[str, int]
    target_status_counts: Mapping[str, int]
    source_ids_sha256: str
    target_ids_sha256: str
    source_content_sha256: str
    target_content_sha256: str
    matched: bool


class LegacyResponseDeliveryExporter:
    def export(
        self,
        database_path: str,
        *,
        exported_at: str | None = None,
    ) -> LegacyDeliverySnapshot:
        path = Path(database_path).expanduser().resolve()
        if not path.is_file():
            raise LegacyContractError(f"legacy delivery database not found: {path}")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(response_deliveries)"
                ).fetchall()
            }
            required = {
                "response_id", "user_id", "conv_id", "request_id", "seq",
                "response_text", "status", "selected_at", "delivered_at",
                "read_at", "identity_metadata_json",
            }
            if not required.issubset(columns):
                missing = sorted(required - columns)
                raise LegacyContractError(
                    f"legacy delivery schema is missing columns: {missing}"
                )
            source_rows = connection.execute(
                "SELECT * FROM response_deliveries "
                "ORDER BY user_id, conv_id, seq, response_id"
            ).fetchall()
        finally:
            connection.close()

        rows = tuple(self._map_row(row) for row in source_rows)
        status_counts = _status_counts(row.target_status for row in rows)
        return LegacyDeliverySnapshot(
            schema_version="dialogpilot.delivery-cutover.v1",
            source_database=str(path),
            exported_at=exported_at or datetime.now(timezone.utc).isoformat(),
            rows=rows,
            row_count=len(rows),
            status_counts=status_counts,
            ids_sha256=_ids_hash(row.response_id for row in rows),
            content_sha256=_snapshot_content_hash(rows),
        )

    @staticmethod
    def _map_row(row: sqlite3.Row) -> LegacyDeliveryRow:
        try:
            metadata = json.loads(row["identity_metadata_json"] or "{}")
        except json.JSONDecodeError as exc:
            raise LegacyContractError(
                f"response {row['response_id']} has invalid identity metadata"
            ) from exc
        if not isinstance(metadata, dict):
            raise LegacyContractError(
                f"response {row['response_id']} identity metadata must be an object"
            )
        required_identity = ("tenant_id", "invocation_key")
        missing = [key for key in required_identity if not str(metadata.get(key) or "")]
        if missing:
            raise LegacyContractError(
                f"response {row['response_id']} lacks authoritative identity: {missing}"
            )
        for metadata_key, column in (
            ("user_id", "user_id"),
            ("conversation_id", "conv_id"),
            ("request_id", "request_id"),
        ):
            value = metadata.get(metadata_key)
            if value is not None and str(value) != str(row[column]):
                raise LegacyContractError(
                    f"response {row['response_id']} identity scope conflicts on "
                    f"{metadata_key}"
                )
        legacy_status = str(row["status"])
        if legacy_status not in LEGACY_STATUS_MAP:
            raise LegacyContractError(
                f"response {row['response_id']} has unsupported status {legacy_status!r}"
            )
        base = {
            "response_id": str(row["response_id"]),
            "tenant_id": str(metadata["tenant_id"]),
            "user_id": str(row["user_id"]),
            "conversation_id": str(row["conv_id"]),
            "request_id": str(row["request_id"]),
            "invocation_key": str(metadata["invocation_key"]),
            "legacy_seq": int(row["seq"]),
            "response_text": str(row["response_text"]),
            "legacy_status": legacy_status,
            "target_status": LEGACY_STATUS_MAP[legacy_status],
            "selected_at": _canonical_timestamp(row["selected_at"]),
            "delivered_at": _canonical_timestamp(row["delivered_at"]),
            "read_at": _canonical_timestamp(row["read_at"]),
            "target_outbox_id": _stable_id("legacy-delivery-outbox", row["response_id"]),
        }
        return LegacyDeliveryRow(
            **base,
            row_sha256=content_hash(base),
        )


class PostgresResponseDeliveryBackfill:
    def __init__(self, pool: PostgresPool):
        self.pool = pool

    def apply(
        self,
        snapshot: LegacyDeliverySnapshot,
        *,
        mode: BackfillMode = BackfillMode.SNAPSHOT,
    ) -> DeliveryReconciliationReport:
        with self.pool.transaction() as connection:
            target_count = connection.execute(
                "SELECT count(*) FROM dialogpilot_app.response_deliveries"
            ).fetchone()[0]
            if target_count:
                report = self._reconcile(connection, snapshot)
                if report.matched:
                    return report
                if mode is BackfillMode.SNAPSHOT:
                    raise TargetNotEmptyError(
                        "target response_deliveries is non-empty and does not match "
                        "snapshot"
                    )
            for row in snapshot.rows:
                exists = connection.execute(
                    "SELECT 1 FROM dialogpilot_app.response_deliveries "
                    "WHERE publication_id=%s", (row.response_id,),
                ).fetchone()
                if exists is None:
                    self._insert(connection, row)
            report = self._reconcile(connection, snapshot)
            if not report.matched:
                raise ReconciliationError(
                    "target does not exactly match final delta snapshot"
                )
            return report

    def reconcile(
        self, snapshot: LegacyDeliverySnapshot,
    ) -> DeliveryReconciliationReport:
        with self.pool.transaction() as connection:
            return self._reconcile(connection, snapshot)

    @staticmethod
    def _insert(connection, row: LegacyDeliveryRow) -> None:
        scope = (row.tenant_id, row.user_id, row.conversation_id)
        conversation = connection.execute("""
            SELECT next_turn_seq, next_event_seq, next_publication_seq
            FROM dialogpilot_app.conversations
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            FOR UPDATE
        """, scope).fetchone()
        if conversation is None:
            raise LegacyContractError(
                f"target conversation does not exist for response {row.response_id}"
            )
        invocation = connection.execute("""
            SELECT request_id FROM dialogpilot_app.workflow_invocations
            WHERE invocation_key=%s AND tenant_id=%s AND user_id=%s
              AND conversation_id=%s
        """, (row.invocation_key, *scope)).fetchone()
        if invocation is None or invocation[0] != row.request_id:
            raise LegacyContractError(
                f"target invocation binding is absent or conflicts for {row.response_id}"
            )
        sequence_collision = connection.execute("""
            SELECT 1 FROM dialogpilot_app.response_deliveries
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s AND seq=%s
        """, (*scope, row.legacy_seq)).fetchone()
        if sequence_collision:
            raise LegacyContractError(
                f"legacy publication sequence collides for response {row.response_id}"
            )

        turn_key = _stable_id("legacy-publication-turn", row.response_id)
        turn_id = _stable_id("legacy-publication-turn-id", row.response_id)
        event_id = _stable_id("legacy-publication-event", row.response_id)
        event_operation = _stable_id(
            "legacy-publication-event-operation", row.response_id,
        )
        operation_key = _stable_id("legacy-delivery-operation", row.response_id)
        publication_payload = {
            "response_id": row.response_id,
            "response": row.response_text,
            "producer": "legacy-sqlite-import",
            "verification": {"status": "LEGACY_UNAVAILABLE"},
        }
        connection.execute("""
            INSERT INTO dialogpilot_app.conversation_turns (
                turn_key, turn_id, tenant_id, user_id, conversation_id, seq,
                role, content, content_sha256, request_id, invocation_key,
                metadata, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,'assistant',%s,%s,%s,%s,%s,%s)
        """, (
            turn_key, turn_id, *scope, int(conversation[0]), row.response_text,
            content_hash({"role": "assistant", "content": row.response_text}),
            row.request_id, row.invocation_key,
            Jsonb({"legacy_response_id": row.response_id}), row.selected_at,
        ))
        event_payload = {
            "publication_id": row.response_id,
            "publication_kind": "final_response",
            "outbound_turn_key": turn_key,
            "migration": "dialogpilot.delivery-cutover.v1",
        }
        connection.execute("""
            INSERT INTO dialogpilot_app.conversation_events (
                event_id, operation_key, tenant_id, user_id, conversation_id, seq,
                event_type, payload, content_sha256, request_id, invocation_key,
                created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,'LEGACY_FINAL_RESPONSE_IMPORTED',
                      %s,%s,%s,%s,%s)
        """, (
            event_id, event_operation, *scope, int(conversation[1]),
            Jsonb(event_payload), content_hash({
                "event_type": "LEGACY_FINAL_RESPONSE_IMPORTED",
                "payload": event_payload,
            }), row.request_id, row.invocation_key, row.selected_at,
        ))
        evidence_sha = row.row_sha256
        unavailable_index_sha = content_hash({
            "legacy_index_manifest": "unavailable",
        })
        reconcile_deadline = (
            datetime.fromisoformat(row.selected_at) + timedelta(days=30)
        ).isoformat()
        connection.execute("""
            INSERT INTO dialogpilot_app.response_deliveries (
                publication_id, publication_kind, delivery_operation_key,
                tenant_id, user_id, conversation_id, seq, invocation_key,
                payload, content_sha256, status, created_at, delivered_at, read_at,
                command_fingerprint, outbound_turn_key, outbound_event_id,
                candidate_id, verifier_status, verification, evidence_sha256,
                bundle_version, index_manifest_sha256, connector_capability,
                attempt, max_attempts, retry_policy_version, reconcile_deadline,
                last_error_code
            ) VALUES (
                %s,'final_response',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,'LEGACY_UNAVAILABLE',%s,%s,'legacy-unversioned',%s,
                'NONE',0,1,'legacy-no-retry-v1',%s,%s
            )
        """, (
            row.response_id, operation_key, *scope, row.legacy_seq,
            row.invocation_key, Jsonb(publication_payload),
            content_hash({"kind": "final_response", "payload": publication_payload}),
            row.target_status, row.selected_at, row.delivered_at, row.read_at,
            row.row_sha256, turn_key, event_id, row.response_id,
            Jsonb({"status": "LEGACY_UNAVAILABLE", "source_row": row.row_sha256}),
            evidence_sha, unavailable_index_sha, reconcile_deadline,
            (
                "LEGACY_SEND_EFFECT_UNKNOWN"
                if row.target_status == "DELIVERY_UNCERTAIN" else None
            ),
        ))
        connection.execute("""
            INSERT INTO dialogpilot_app.delivery_outbox (
                outbox_id, publication_id, delivery_operation_key, payload,
                available_at, acknowledged_at, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            row.target_outbox_id, row.response_id, operation_key,
            Jsonb({
                "publication_id": row.response_id,
                "migration": "dialogpilot.delivery-cutover.v1",
                "automatic_send_disabled": True,
            }), row.selected_at, row.selected_at, row.selected_at,
        ))
        connection.execute("""
            UPDATE dialogpilot_app.conversations
            SET next_turn_seq=GREATEST(next_turn_seq, %s),
                next_event_seq=GREATEST(next_event_seq, %s),
                next_publication_seq=GREATEST(next_publication_seq, %s),
                updated_at=GREATEST(updated_at, %s::timestamptz)
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (
            int(conversation[0]) + 1, int(conversation[1]) + 1,
            row.legacy_seq + 1, row.selected_at, *scope,
        ))

    @staticmethod
    def _reconcile(connection, snapshot):
        with connection.cursor(row_factory=dict_row) as cursor:
            target = cursor.execute("""
                SELECT d.publication_id, d.tenant_id, d.user_id,
                       d.conversation_id, i.request_id, d.invocation_key, d.seq,
                       d.payload->>'response' AS response_text, d.status,
                       d.created_at, d.delivered_at, d.read_at, o.outbox_id
                FROM dialogpilot_app.response_deliveries d
                JOIN dialogpilot_app.workflow_invocations i
                  ON i.invocation_key=d.invocation_key
                JOIN dialogpilot_app.delivery_outbox o
                  ON o.publication_id=d.publication_id
                ORDER BY d.user_id, d.conversation_id, d.seq, d.publication_id
            """).fetchall()
        target_records = [
            {
                "publication_id": row["publication_id"],
                "tenant_id": row["tenant_id"],
                "user_id": row["user_id"],
                "conversation_id": row["conversation_id"],
                "request_id": row["request_id"],
                "invocation_key": row["invocation_key"],
                "seq": int(row["seq"]),
                "response_text": row["response_text"],
                "status": row["status"],
                "selected_at": row["created_at"].isoformat(),
                "delivered_at": _iso(row["delivered_at"]),
                "read_at": _iso(row["read_at"]),
                "outbox_id": row["outbox_id"],
            }
            for row in target
        ]
        source_records = [row.comparison_record() for row in snapshot.rows]
        source_hash = _canonical_hash(source_records)
        target_hash = _canonical_hash(target_records)
        target_statuses = _status_counts(row["status"] for row in target_records)
        target_ids = _ids_hash(row["publication_id"] for row in target_records)
        report = DeliveryReconciliationReport(
            schema_version="dialogpilot.delivery-reconcile.v1",
            snapshot_sha256=snapshot.content_sha256,
            source_count=snapshot.row_count,
            target_count=len(target_records),
            source_status_counts=dict(snapshot.status_counts),
            target_status_counts=target_statuses,
            source_ids_sha256=snapshot.ids_sha256,
            target_ids_sha256=target_ids,
            source_content_sha256=source_hash,
            target_content_sha256=target_hash,
            matched=(
                snapshot.row_count == len(target_records)
                and dict(snapshot.status_counts) == target_statuses
                and snapshot.ids_sha256 == target_ids
                and source_hash == target_hash
            ),
        )
        return report


def write_snapshot(snapshot: LegacyDeliverySnapshot, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def read_snapshot(path: str) -> LegacyDeliverySnapshot:
    return LegacyDeliverySnapshot.from_dict(json.loads(Path(path).read_text("utf-8")))


def _snapshot_content_hash(rows: tuple[LegacyDeliveryRow, ...]) -> str:
    return _canonical_hash([asdict(row) for row in rows])


def _ids_hash(values) -> str:
    return _canonical_hash(sorted(str(value) for value in values))


def _status_counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def _require_unique(values, field: str) -> None:
    seen = set()
    for value in values:
        if value in seen:
            raise LegacyContractError(f"snapshot has duplicate {field}: {value}")
        seen.add(value)


def _stable_id(namespace: str, value: object) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"{namespace}:v1:{digest}"


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical_timestamp(value) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise LegacyContractError("legacy delivery timestamp lacks timezone")
    return parsed.astimezone(timezone.utc).isoformat()
