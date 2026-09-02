"""Runtime schema compatibility is closed, linear and fail-closed."""

import pytest

from core.schema_version_registry import (
    SchemaCompatibilityError,
    SchemaVersionRegistry,
)
from infrastructure.postgres import PostgresMigrationRunner


def test_runtime_schema_manifest_is_reproducible_and_linear():
    runner = PostgresMigrationRunner(
        "postgresql://registry:registry@localhost/registry"
    )
    migrations = runner.revision_manifest()
    assert migrations == runner.revision_manifest()
    assert migrations[0]["down_revision"] is None
    assert migrations[-1]["revision"] == SchemaVersionRegistry.postgres.current_version
    assert all(
        row["down_revision"] == migrations[index - 1]["revision"]
        for index, row in enumerate(migrations[1:], 1)
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
