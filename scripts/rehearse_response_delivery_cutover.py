#!/usr/bin/env python3
"""Create one deterministic local M1-T03A cutover fixture and activate it."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory
from infrastructure.delivery_binding import (
    PostgresDeliveryBindingRepository,
    ResponseDeliveryCutoverCoordinator,
)
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.response_delivery_cutover import PostgresResponseDeliveryBackfill
from services.response_delivery import ResponseDeliveryService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--sqlite-path", required=True)
    args = parser.parse_args()
    PostgresMigrationRunner(
        args.database_url, actor="t03a-rehearsal", application_version="local",
    ).upgrade()
    pool = PostgresPool(PostgresPoolConfig(args.database_url, min_size=1, max_size=2))
    pool.open()
    try:
        identity = IdentityFactory(lambda: "unused").create_invocation(
            tenant_id="tenant-rehearsal",
            user_id="user-rehearsal",
            conversation_id="conversation-rehearsal",
            request_id="request-rehearsal",
        )
        PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
            identity=identity,
            message="rehearsal question",
            pinned_versions={"bundle": "legacy", "runtime": "compat-v1"},
            created_at="2026-09-02T08:00:00+00:00",
        ))
        legacy = ResponseDeliveryService(args.sqlite_path)
        legacy.select_response(
            user_id=str(identity.user_id),
            conv_id=str(identity.conversation_id),
            request_id=str(identity.request_id),
            response_text="rehearsal answer",
            identity_metadata=identity.metadata(),
        )
        coordinator = ResponseDeliveryCutoverCoordinator(
            legacy=legacy,
            legacy_database_path=args.sqlite_path,
            binding=PostgresDeliveryBindingRepository(pool),
            backfill=PostgresResponseDeliveryBackfill(pool),
        )
        coordinator.prepare_freeze(
            freeze_id="local-restore-rehearsal-v1", actor="local-operator",
        )
        result = coordinator.finalize_and_activate(
            freeze_id="local-restore-rehearsal-v1",
            actor="local-operator",
            switched_at="2026-09-02T08:05:00+00:00",
        )
        coordinator.assert_worker_resume_safe()
        print(json.dumps(asdict(result), sort_keys=True, default=str))
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
