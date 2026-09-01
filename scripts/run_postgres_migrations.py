#!/usr/bin/env python3
"""Apply or verify the repository-owned PostgreSQL migration chain."""
from __future__ import annotations

import argparse
import json
import os

from infrastructure.postgres import PostgresMigrationRunner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--config", default="alembic.ini")
    parser.add_argument("--actor", default="migration-runner")
    parser.add_argument("--application-version", default=os.getenv("GIT_COMMIT_SHA", "unknown"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    runner = PostgresMigrationRunner(
        args.database_url,
        config_path=args.config,
        actor=args.actor,
        application_version=args.application_version,
    )
    result = runner.verify() if args.verify_only else runner.upgrade()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
