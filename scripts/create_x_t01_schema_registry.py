"""Render the repository-owned X-T01 schema/version registry artifact."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from core.schema_version_registry import SchemaVersionRegistry
from infrastructure.postgres import PostgresMigrationRunner


def build_registry() -> dict[str, object]:
    runner = PostgresMigrationRunner("postgresql://registry:registry@localhost/registry")
    contracts = {
        "postgres": asdict(SchemaVersionRegistry.postgres),
        "agent_checkpoint": asdict(SchemaVersionRegistry.agent_checkpoint),
        "agent_code": asdict(SchemaVersionRegistry.agent_code),
    }
    for value in contracts.values():
        value["strategy"] = value["strategy"].value
    payload = {
        "registry_version": SchemaVersionRegistry.version,
        "contracts": contracts,
        "postgres_migrations": list(runner.revision_manifest()),
        "data_location_transitions": [
            {"revision": row[0], "registry_version": row[1], "fingerprint": row[2]}
            for row in SchemaVersionRegistry.data_location_transitions
        ],
        "downgrade_policy": "FORBIDDEN_FORWARD_FIX_OR_FULL_RESTORE",
    }
    payload = json.loads(json.dumps(payload, sort_keys=True))
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return {
        "schema_version": "x-t01-schema-registry-artifact-v1",
        "payload": payload,
        "payload_sha256": hashlib.sha256(encoded).hexdigest(),
        "verification_status": "BUILD_LOCAL_RESTORE_PRODUCTION_SNAPSHOT_PENDING",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_registry(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
