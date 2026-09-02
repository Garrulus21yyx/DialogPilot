"""PostgreSQL-backed deletion epoch check for registered durable writes."""
from __future__ import annotations

from dataclasses import dataclass

from application.data_location_registry import (
    DataLocationRegistry,
    DataLocationWriteDenied,
    DataWriteIntent,
    default_registry_path,
)
from infrastructure.postgres import PostgresPool


@dataclass(frozen=True)
class DataWriteAuthorization:
    registry_version: str
    registry_fingerprint: str
    location_id: str
    deletion_epoch: int
    subject_exists: bool


class PostgresDataLocationWriteFence:
    def __init__(
        self,
        pool: PostgresPool,
        registry: DataLocationRegistry | None = None,
    ):
        self.pool = pool
        self.registry = registry or DataLocationRegistry.load(default_registry_path())

    def authorize(self, intent: DataWriteIntent) -> DataWriteAuthorization:
        location = self.registry.authorize_contract(intent)
        with self.pool.transaction() as connection:
            installed = connection.execute("""
                SELECT 1 FROM dialogpilot_platform.data_location_registry_revisions
                WHERE registry_version=%s AND artifact_fingerprint=%s
            """, (self.registry.version, self.registry.fingerprint)).fetchone()
            if installed is None:
                raise DataLocationWriteDenied(
                    "approved registry artifact is not installed in target database"
                )
            row = connection.execute("""
                SELECT deletion_epoch, deleted_at IS NOT NULL
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                FOR SHARE
            """, (
                intent.subject.tenant_id, intent.subject.user_id,
                intent.subject.conversation_id,
            )).fetchone()
        if row is None:
            if not location.allow_subject_create or intent.expected_deletion_epoch != 0:
                raise DataLocationWriteDenied(
                    "subject does not exist and location cannot create it"
                )
            return DataWriteAuthorization(
                self.registry.version, self.registry.fingerprint,
                location.location_id, 0, False,
            )
        current_epoch, deleted = int(row[0]), bool(row[1])
        if deleted:
            raise DataLocationWriteDenied("subject is deletion-fenced")
        if current_epoch != intent.expected_deletion_epoch:
            raise DataLocationWriteDenied(
                "subject deletion epoch changed before durable write"
            )
        return DataWriteAuthorization(
            self.registry.version, self.registry.fingerprint,
            location.location_id, current_epoch, True,
        )
