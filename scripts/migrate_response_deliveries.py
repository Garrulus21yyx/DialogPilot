#!/usr/bin/env python3
"""Operate the M1-T03A legacy delivery snapshot/backfill/reconcile workflow."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.response_delivery_cutover import (
    LegacyResponseDeliveryExporter,
    PostgresResponseDeliveryBackfill,
    read_snapshot,
    write_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--sqlite-path", required=True)
    export.add_argument("--output", required=True)
    for name in ("backfill", "reconcile"):
        command = subparsers.add_parser(name)
        command.add_argument("--snapshot", required=True)
        command.add_argument("--database-url", required=True)
    args = parser.parse_args()

    if args.command == "export":
        snapshot = LegacyResponseDeliveryExporter().export(args.sqlite_path)
        write_snapshot(snapshot, args.output)
        print(json.dumps({
            "schema_version": snapshot.schema_version,
            "row_count": snapshot.row_count,
            "status_counts": dict(snapshot.status_counts),
            "ids_sha256": snapshot.ids_sha256,
            "content_sha256": snapshot.content_sha256,
            "output": args.output,
        }, sort_keys=True))
        return 0

    PostgresMigrationRunner(args.database_url).verify()
    pool = PostgresPool(PostgresPoolConfig(args.database_url))
    pool.open()
    try:
        repository = PostgresResponseDeliveryBackfill(pool)
        snapshot = read_snapshot(args.snapshot)
        report = (
            repository.apply(snapshot)
            if args.command == "backfill"
            else repository.reconcile(snapshot)
        )
        print(json.dumps(asdict(report), sort_keys=True))
        return 0 if report.matched else 2
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
