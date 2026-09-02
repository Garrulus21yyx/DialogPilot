"""Crash-recoverable single-owner binding for ResponseDelivery cutover."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from psycopg.rows import dict_row

from infrastructure.postgres import PostgresPool
from infrastructure.response_delivery_cutover import (
    BackfillMode,
    DeliveryReconciliationReport,
    LegacyDeliverySnapshot,
    LegacyResponseDeliveryExporter,
    PostgresResponseDeliveryBackfill,
    ReconciliationError,
)
from services.response_delivery import ResponseDeliveryService


class DeliveryBindingError(RuntimeError):
    pass


class DeliveryBindingConflict(DeliveryBindingError):
    pass


class DeliveryRepositoryState(str, Enum):
    SQLITE_ACTIVE = "SQLITE_ACTIVE"
    FROZEN = "FROZEN"
    POSTGRES_ACTIVE = "POSTGRES_ACTIVE"


@dataclass(frozen=True)
class DeliveryBinding:
    state: DeliveryRepositoryState
    generation: int
    freeze_id: str | None
    snapshot_sha256: str | None
    switched_at: str | None


@dataclass(frozen=True)
class DeliveryCutoverResult:
    binding: DeliveryBinding
    snapshot: LegacyDeliverySnapshot
    reconciliation: DeliveryReconciliationReport
    legacy_writer_state: str


class PostgresDeliveryBindingRepository:
    def __init__(self, pool: PostgresPool):
        self.pool = pool

    def get(self) -> DeliveryBinding:
        with self.pool.transaction() as connection:
            return self._get(connection, lock=False)

    def freeze(self, *, freeze_id: str, actor: str, reason: str) -> DeliveryBinding:
        _required(freeze_id, "freeze_id")
        _required(actor, "actor")
        _required(reason, "reason")
        with self.pool.transaction() as connection:
            current = self._get(connection, lock=True)
            if (
                current.state is DeliveryRepositoryState.FROZEN
                and current.freeze_id == freeze_id
            ):
                return current
            if current.state is not DeliveryRepositoryState.SQLITE_ACTIVE:
                raise DeliveryBindingConflict(
                    f"cannot freeze delivery binding from {current.state.value}"
                )
            return self._transition(
                connection, current,
                target=DeliveryRepositoryState.FROZEN,
                freeze_id=freeze_id,
                snapshot_sha256=None,
                actor=actor,
                reason=reason,
            )

    def abort_before_switch(
        self, *, freeze_id: str, actor: str, reason: str,
    ) -> DeliveryBinding:
        with self.pool.transaction() as connection:
            current = self._get(connection, lock=True)
            if current.state is DeliveryRepositoryState.SQLITE_ACTIVE:
                return current
            if current.state is DeliveryRepositoryState.POSTGRES_ACTIVE:
                raise DeliveryBindingConflict(
                    "PostgreSQL-active delivery cannot roll back to SQLite"
                )
            if current.freeze_id != freeze_id:
                raise DeliveryBindingConflict("freeze ID does not own binding")
            return self._transition(
                connection, current,
                target=DeliveryRepositoryState.SQLITE_ACTIVE,
                freeze_id=None,
                snapshot_sha256=None,
                actor=actor,
                reason=reason,
            )

    def activate_postgres(
        self,
        *,
        freeze_id: str,
        snapshot: LegacyDeliverySnapshot,
        actor: str,
        reason: str,
        switched_at: str,
    ) -> tuple[DeliveryBinding, DeliveryReconciliationReport]:
        with self.pool.transaction() as connection:
            current = self._get(connection, lock=True)
            report = PostgresResponseDeliveryBackfill._reconcile(
                connection, snapshot,
            )
            if not report.matched:
                raise ReconciliationError(
                    "binding switch requires exact final-snapshot reconciliation"
                )
            if current.state is DeliveryRepositoryState.POSTGRES_ACTIVE:
                if (
                    current.freeze_id == freeze_id
                    and current.snapshot_sha256 == snapshot.content_sha256
                ):
                    return current, report
                raise DeliveryBindingConflict(
                    "PostgreSQL is already active for a different cutover"
                )
            if (
                current.state is not DeliveryRepositoryState.FROZEN
                or current.freeze_id != freeze_id
            ):
                raise DeliveryBindingConflict(
                    "matching frozen binding is required for activation"
                )
            activated = self._transition(
                connection, current,
                target=DeliveryRepositoryState.POSTGRES_ACTIVE,
                freeze_id=freeze_id,
                snapshot_sha256=snapshot.content_sha256,
                actor=actor,
                reason=reason,
                switched_at=switched_at,
            )
            return activated, report

    def assert_postgres_active(self) -> DeliveryBinding:
        binding = self.get()
        if binding.state is not DeliveryRepositoryState.POSTGRES_ACTIVE:
            raise DeliveryBindingConflict(
                f"PostgreSQL delivery writer is not active: {binding.state.value}"
            )
        return binding

    @staticmethod
    def _get(connection, *, lock: bool) -> DeliveryBinding:
        suffix = " FOR UPDATE" if lock else ""
        with connection.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                "SELECT state, generation, freeze_id, snapshot_sha256, switched_at "
                "FROM dialogpilot_platform.delivery_repository_binding "
                "WHERE singleton=TRUE" + suffix,
            ).fetchone()
        if row is None:
            raise DeliveryBindingError("delivery repository binding is missing")
        return DeliveryBinding(
            state=DeliveryRepositoryState(row["state"]),
            generation=int(row["generation"]),
            freeze_id=row["freeze_id"],
            snapshot_sha256=row["snapshot_sha256"],
            switched_at=(
                row["switched_at"].isoformat() if row["switched_at"] else None
            ),
        )

    @staticmethod
    def _transition(
        connection,
        current: DeliveryBinding,
        *,
        target: DeliveryRepositoryState,
        freeze_id: str | None,
        snapshot_sha256: str | None,
        actor: str,
        reason: str,
        switched_at: str | None = None,
    ) -> DeliveryBinding:
        generation = current.generation + 1
        event_id = _event_id(current.generation, generation, target, freeze_id)
        row = connection.execute("""
            UPDATE dialogpilot_platform.delivery_repository_binding
            SET state=%s, generation=%s, freeze_id=%s, snapshot_sha256=%s,
                switched_at=%s, updated_at=transaction_timestamp()
            WHERE singleton=TRUE AND generation=%s
            RETURNING state, generation, freeze_id, snapshot_sha256, switched_at
        """, (
            target.value, generation, freeze_id, snapshot_sha256,
            switched_at, current.generation,
        )).fetchone()
        if row is None:
            raise DeliveryBindingConflict("delivery binding CAS lost")
        connection.execute("""
            INSERT INTO dialogpilot_platform.delivery_binding_events (
                event_id, prior_state, target_state, prior_generation,
                target_generation, freeze_id, snapshot_sha256, actor, reason,
                created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,transaction_timestamp())
        """, (
            event_id, current.state.value, target.value, current.generation,
            generation, freeze_id, snapshot_sha256, actor, reason,
        ))
        return DeliveryBinding(
            state=DeliveryRepositoryState(row[0]),
            generation=int(row[1]),
            freeze_id=row[2], snapshot_sha256=row[3],
            switched_at=row[4].isoformat() if row[4] else None,
        )


class ResponseDeliveryCutoverCoordinator:
    """Orders cross-database steps so every crash leaves at most one writer."""

    def __init__(
        self,
        *,
        legacy: ResponseDeliveryService,
        legacy_database_path: str,
        binding: PostgresDeliveryBindingRepository,
        backfill: PostgresResponseDeliveryBackfill,
        fault_hook: Callable[[str], None] | None = None,
    ):
        self.legacy = legacy
        self.legacy_database_path = legacy_database_path
        self.binding = binding
        self.backfill = backfill
        self.fault_hook = fault_hook or (lambda _stage: None)

    def prepare_freeze(self, *, freeze_id: str, actor: str) -> DeliveryBinding:
        current = self.binding.get()
        if current.state is DeliveryRepositoryState.POSTGRES_ACTIVE:
            raise DeliveryBindingConflict("PostgreSQL delivery is already active")
        self.legacy.freeze_writes(freeze_id)
        self.fault_hook("after_legacy_freeze")
        frozen = self.binding.freeze(
            freeze_id=freeze_id,
            actor=actor,
            reason="legacy selection, ACK and worker claims stopped",
        )
        self.fault_hook("after_binding_freeze")
        return frozen

    def abort_before_switch(
        self, *, freeze_id: str, actor: str, reason: str,
    ) -> DeliveryBinding:
        binding = self.binding.abort_before_switch(
            freeze_id=freeze_id, actor=actor, reason=reason,
        )
        self.fault_hook("after_binding_abort")
        self.legacy.unfreeze_before_cutover(freeze_id)
        self.fault_hook("after_legacy_unfreeze")
        return binding

    def finalize_and_activate(
        self,
        *,
        freeze_id: str,
        actor: str,
        switched_at: str,
    ) -> DeliveryCutoverResult:
        binding = self.binding.get()
        legacy_state, legacy_freeze_id = self.legacy.writer_state()
        if legacy_state not in {"FROZEN", "RETIRED"}:
            raise DeliveryBindingConflict("legacy writer must be frozen")
        if legacy_freeze_id != freeze_id:
            raise DeliveryBindingConflict("legacy freeze ID does not match")
        if (
            binding.state is not DeliveryRepositoryState.POSTGRES_ACTIVE
            and (
                binding.state is not DeliveryRepositoryState.FROZEN
                or binding.freeze_id != freeze_id
            )
        ):
            raise DeliveryBindingConflict("PostgreSQL binding is not matching freeze")

        snapshot = LegacyResponseDeliveryExporter().export(
            self.legacy_database_path,
        )
        self.fault_hook("after_final_export")
        report = self.backfill.apply(snapshot, mode=BackfillMode.FINAL_DELTA)
        self.fault_hook("after_final_backfill")
        activated, report = self.binding.activate_postgres(
            freeze_id=freeze_id,
            snapshot=snapshot,
            actor=actor,
            reason="final delta reconciled; PostgreSQL becomes sole owner",
            switched_at=switched_at,
        )
        self.fault_hook("after_binding_switch")
        self.legacy.retire_writes(freeze_id)
        self.fault_hook("after_legacy_retire")
        return DeliveryCutoverResult(
            binding=activated,
            snapshot=snapshot,
            reconciliation=report,
            legacy_writer_state=self.legacy.writer_state()[0],
        )

    def assert_worker_resume_safe(self) -> DeliveryBinding:
        binding = self.binding.assert_postgres_active()
        legacy_state, _ = self.legacy.writer_state()
        if legacy_state != "RETIRED":
            raise DeliveryBindingConflict(
                "legacy writer must be retired before PostgreSQL worker resume"
            )
        return binding


def _event_id(
    prior_generation: int,
    target_generation: int,
    target: DeliveryRepositoryState,
    freeze_id: str | None,
) -> str:
    raw = (
        f"v1\0{prior_generation}\0{target_generation}\0{target.value}\0"
        f"{freeze_id or ''}"
    ).encode("utf-8")
    return f"delivery-binding-event:v1:{hashlib.sha256(raw).hexdigest()}"


def _required(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    return normalized
