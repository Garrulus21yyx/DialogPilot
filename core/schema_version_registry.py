"""Closed schema compatibility registry for durable owners."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SchemaCompatibilityError(RuntimeError):
    pass


class MigrationStrategy(str, Enum):
    FORWARD_ONLY = "forward_only"
    READ_MIGRATE = "read_migrate"


@dataclass(frozen=True)
class SchemaContract:
    owner: str
    current_version: str
    readable_versions: tuple[str, ...]
    strategy: MigrationStrategy

    def validate_read(self, version: str) -> None:
        if version not in self.readable_versions:
            raise SchemaCompatibilityError(
                f"{self.owner} cannot read schema version {version!r}"
            )


class SchemaVersionRegistry:
    version = "schema-version-registry-v1"
    postgres = SchemaContract(
        "postgres-domain", "20260905_0032", ("20260905_0032",),
        MigrationStrategy.FORWARD_ONLY,
    )
    agent_checkpoint = SchemaContract(
        "agent-checkpoint", "react-checkpoint-v1",
        ("legacy-react-checkpoint-v0", "react-checkpoint-v1"),
        MigrationStrategy.READ_MIGRATE,
    )
    agent_code = SchemaContract(
        "agent-runtime-code", "legacy-react-engine-v1",
        ("legacy-react-engine-v0", "legacy-react-engine-v1"),
        MigrationStrategy.READ_MIGRATE,
    )
    data_location_transitions = (
        ("20260902_0007", "v1", "92760d381381231733d03ac8f11b6cd4d8aa75931c68988aa01719545c40fd28"),
        ("20260902_0009", "v2", "51e227f466f05185b20c8175b03dbfc852450b1644c371a23ccf5dcedbd5d745"),
        ("20260902_0012", "v3", "14bda1d84d888883c2d4f42bfbc0b88c04860441c0d243b2b01e3a9cdfc98ade"),
        ("20260902_0013", "v4", "2aae62ba01ac4195ae50a7dbd7b619f433d5a800b3fce8698a1e3a9a3f49f502"),
        ("20260902_0018", "v5", "cd86f3a57adaaa2e25fb4cc6c9e59fb64ebc43f12243463ca83335198382a1e5"),
        ("20260902_0020", "v6", "cbf99367f299650413996974cf981a58c8485d984d8df9cea7d0d0790babedf3"),
        ("20260903_0029", "v7", "159e473579c9f157560410142302023de2b39a15e8869b8679cc8e40d16e479f"),
    )

    @classmethod
    def validate_agent_resume(cls, *, checkpoint_version: str, code_version: str) -> None:
        cls.agent_checkpoint.validate_read(checkpoint_version)
        cls.agent_code.validate_read(code_version)
