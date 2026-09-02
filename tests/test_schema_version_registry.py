"""X-T01 schema registry is closed, reproducible and fail-closed."""
import json
from pathlib import Path

import pytest

from core.schema_version_registry import (
    SchemaCompatibilityError,
    SchemaVersionRegistry,
)
from scripts.create_x_t01_schema_registry import build_registry


ROOT = Path(__file__).resolve().parents[1]


def test_schema_registry_artifact_is_reproducible_and_linear():
    frozen = json.loads((
        ROOT / "governance/schema/x-t01-schema-registry-v1.json"
    ).read_text(encoding="utf-8"))

    assert frozen == build_registry()
    migrations = frozen["payload"]["postgres_migrations"]
    assert migrations[0]["down_revision"] is None
    assert migrations[-1]["revision"] == SchemaVersionRegistry.postgres.current_version
    assert all(
        row["down_revision"] == migrations[index - 1]["revision"]
        for index, row in enumerate(migrations[1:], 1)
    )
    assert frozen["payload"]["downgrade_policy"] == (
        "FORBIDDEN_FORWARD_FIX_OR_FULL_RESTORE"
    )


def test_agent_resume_compatibility_is_separate_from_postgres_schema():
    SchemaVersionRegistry.validate_agent_resume(
        checkpoint_version="legacy-react-checkpoint-v0",
        code_version="legacy-react-engine-v0",
    )
    SchemaVersionRegistry.validate_agent_resume(
        checkpoint_version="react-checkpoint-v1",
        code_version="legacy-react-engine-v1",
    )

    with pytest.raises(SchemaCompatibilityError):
        SchemaVersionRegistry.validate_agent_resume(
            checkpoint_version=SchemaVersionRegistry.postgres.current_version,
            code_version="legacy-react-engine-v1",
        )
