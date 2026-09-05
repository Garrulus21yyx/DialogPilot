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


@pytest.mark.parametrize("version", ("legacy-react-checkpoint-v0", "react-checkpoint-v1", "unknown"))
def test_domain_schema_does_not_accept_retired_agent_checkpoint_versions(version):
    SchemaVersionRegistry.postgres.validate_read(SchemaVersionRegistry.postgres.current_version)
    with pytest.raises(SchemaCompatibilityError):
        SchemaVersionRegistry.postgres.validate_read(version)
