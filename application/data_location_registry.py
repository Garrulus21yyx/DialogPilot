"""Versioned data-location registration and pre-write authorization contract."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol


class DataLocationError(RuntimeError):
    pass


class UnknownDataLocation(DataLocationError):
    pass


class DataLocationWriteDenied(DataLocationError):
    pass


class DataLocationArtifactInvalid(DataLocationError):
    pass


class LocationReadiness(str, Enum):
    REGISTERED = "REGISTERED"
    WRITE_APPROVED = "WRITE_APPROVED"


class DurableWriteKind(str, Enum):
    MIGRATION = "MIGRATION"
    BACKFILL = "BACKFILL"
    PRODUCER = "PRODUCER"
    DARK_SHADOW = "DARK_SHADOW"
    RESTORE = "RESTORE"


@dataclass(frozen=True)
class DataSubjectRef:
    tenant_id: str
    user_id: str
    conversation_id: str


@dataclass(frozen=True)
class DataLocationRegistration:
    location_id: str
    owner: str
    schema_version: str
    retention_class: str
    readiness: LocationReadiness
    allowed_producers: tuple[str, ...]
    delete_adapter_id: str | None
    proof_contract_id: str | None
    restore_fence_id: str | None
    allow_subject_create: bool


@dataclass(frozen=True)
class DeletionProof:
    adapter_id: str
    subject: DataSubjectRef
    through_deletion_epoch: int
    proof_sha256: str


class DeleteOrDeidentifyAdapter(Protocol):
    def delete_or_deidentify(
        self,
        subject: DataSubjectRef,
        *,
        through_deletion_epoch: int,
    ) -> DeletionProof: ...


class RestoreFence(Protocol):
    def validate_restore(
        self,
        subject: DataSubjectRef,
        *,
        backup_deletion_epoch: int,
        current_deletion_epoch: int,
    ) -> None: ...


@dataclass(frozen=True)
class DataWriteIntent:
    location_id: str
    producer: str
    kind: DurableWriteKind
    schema_version: str
    retention_class: str
    subject: DataSubjectRef
    expected_deletion_epoch: int

    def __post_init__(self) -> None:
        if self.expected_deletion_epoch < 0:
            raise ValueError("expected deletion epoch must be non-negative")


class DataLocationRegistry:
    schema = "dialogpilot.data-location-registry.v1"
    overlay_schema = "dialogpilot.data-location-registry-overlay.v1"

    def __init__(
        self,
        *,
        version: str,
        approved_by: str,
        registrations: tuple[DataLocationRegistration, ...],
        fingerprint: str,
    ):
        self.version = version
        self.approved_by = approved_by
        self.registrations = registrations
        self.fingerprint = fingerprint
        self._by_id = {item.location_id: item for item in registrations}
        if len(self._by_id) != len(registrations):
            raise DataLocationArtifactInvalid("duplicate data location ID")

    @classmethod
    def load(cls, path: str | Path) -> "DataLocationRegistry":
        artifact_path = Path(path).resolve()
        artifact_raw = json.loads(artifact_path.read_text("utf-8"))
        raw = cls._resolve_artifact(artifact_raw, artifact_path, seen=set())
        if raw.get("schema") != cls.schema:
            raise DataLocationArtifactInvalid("unsupported registry schema")
        if not str(raw.get("version") or "").strip():
            raise DataLocationArtifactInvalid("registry version is required")
        if not str(raw.get("approved_by") or "").strip():
            raise DataLocationArtifactInvalid("registry approver is required")
        raw_locations = raw.get("locations")
        if not isinstance(raw_locations, list) or not raw_locations:
            raise DataLocationArtifactInvalid("registry locations must be non-empty")
        catalog = raw.get("contract_catalog")
        if not isinstance(catalog, dict):
            raise DataLocationArtifactInvalid("contract catalog is required")
        contract_ids = {}
        for contract_kind in (
            "delete_adapters", "proof_contracts", "restore_fences",
        ):
            entries = catalog.get(contract_kind)
            if not isinstance(entries, list):
                raise DataLocationArtifactInvalid(
                    f"contract catalog lacks {contract_kind}"
                )
            contract_ids[contract_kind] = {
                str(entry.get("id"))
                for entry in entries
                if isinstance(entry, dict) and entry.get("status") == "IMPLEMENTED"
            }
        registrations = []
        for item in raw_locations:
            try:
                registration = DataLocationRegistration(
                    location_id=str(item["location_id"]),
                    owner=str(item["owner"]),
                    schema_version=str(item["schema_version"]),
                    retention_class=str(item["retention_class"]),
                    readiness=LocationReadiness(item["readiness"]),
                    allowed_producers=tuple(map(str, item["allowed_producers"])),
                    delete_adapter_id=_optional(item.get("delete_adapter_id")),
                    proof_contract_id=_optional(item.get("proof_contract_id")),
                    restore_fence_id=_optional(item.get("restore_fence_id")),
                    allow_subject_create=bool(item.get("allow_subject_create", False)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise DataLocationArtifactInvalid(
                    f"invalid location registration: {item!r}"
                ) from exc
            cls._validate_registration(registration)
            if registration.readiness is LocationReadiness.WRITE_APPROVED:
                required_contracts = (
                    ("delete_adapters", registration.delete_adapter_id),
                    ("proof_contracts", registration.proof_contract_id),
                    ("restore_fences", registration.restore_fence_id),
                )
                for contract_kind, contract_id in required_contracts:
                    if contract_id not in contract_ids[contract_kind]:
                        raise DataLocationArtifactInvalid(
                            f"write-approved location references unavailable "
                            f"{contract_kind}: {contract_id}"
                        )
            registrations.append(registration)
        return cls(
            version=str(raw["version"]),
            approved_by=str(raw["approved_by"]),
            registrations=tuple(registrations),
            fingerprint=_canonical_hash(
                artifact_raw
                if artifact_raw.get("schema") == cls.schema
                else {"artifact": artifact_raw, "effective_registry": raw}
            ),
        )

    @classmethod
    def _resolve_artifact(
        cls,
        raw: Mapping[str, Any],
        path: Path,
        *,
        seen: set[Path],
    ) -> dict[str, Any]:
        if path in seen:
            raise DataLocationArtifactInvalid("registry overlay cycle")
        if raw.get("schema") == cls.schema:
            return dict(raw)
        if raw.get("schema") != cls.overlay_schema:
            raise DataLocationArtifactInvalid("unsupported registry schema")
        seen.add(path)
        base_ref = str(raw.get("base_artifact") or "").strip()
        if not base_ref or Path(base_ref).is_absolute():
            raise DataLocationArtifactInvalid("overlay base artifact must be relative")
        base_path = (path.parent / base_ref).resolve()
        if path.parent not in base_path.parents:
            raise DataLocationArtifactInvalid("overlay base artifact escapes registry directory")
        try:
            base_raw = json.loads(base_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataLocationArtifactInvalid("overlay base artifact is unavailable") from exc
        effective = cls._resolve_artifact(base_raw, base_path, seen=seen)
        version = str(raw.get("version") or "").strip()
        approved_by = str(raw.get("approved_by") or "").strip()
        if not version or not approved_by:
            raise DataLocationArtifactInvalid("overlay version and approver are required")
        effective["version"] = version
        effective["approved_by"] = approved_by
        additions = raw.get("contract_catalog_additions", {})
        if not isinstance(additions, dict):
            raise DataLocationArtifactInvalid("contract catalog additions must be an object")
        for kind, entries in additions.items():
            if kind not in effective["contract_catalog"] or not isinstance(entries, list):
                raise DataLocationArtifactInvalid("invalid contract catalog addition")
            known = {item["id"] for item in effective["contract_catalog"][kind]}
            for entry in entries:
                if not isinstance(entry, dict) or not str(entry.get("id") or "").strip():
                    raise DataLocationArtifactInvalid("invalid contract catalog entry")
                if entry["id"] in known:
                    raise DataLocationArtifactInvalid("contract catalog ID is immutable")
                effective["contract_catalog"][kind].append(entry)
                known.add(entry["id"])
        location_additions = raw.get("location_additions", [])
        if not isinstance(location_additions, list):
            raise DataLocationArtifactInvalid("location additions must be a list")
        known_locations = {
            str(item.get("location_id")) for item in effective["locations"]
        }
        for entry in location_additions:
            if not isinstance(entry, dict) or not str(
                entry.get("location_id") or ""
            ).strip():
                raise DataLocationArtifactInvalid("invalid location addition")
            if entry["location_id"] in known_locations:
                raise DataLocationArtifactInvalid("location ID is immutable")
            effective["locations"].append(dict(entry))
            known_locations.add(entry["location_id"])
        by_id = {item["location_id"]: item for item in effective["locations"]}
        updates = raw.get("location_updates", [])
        if not isinstance(updates, list):
            raise DataLocationArtifactInvalid("location updates must be a list")
        for update in updates:
            if not isinstance(update, dict) or set(update) != {"location_id", "set"}:
                raise DataLocationArtifactInvalid("invalid location update")
            location_id = str(update["location_id"])
            changes = update["set"]
            if location_id not in by_id or not isinstance(changes, dict):
                raise DataLocationArtifactInvalid("location update target is unknown")
            immutable = {"location_id", "owner", "retention_class"}
            if immutable.intersection(changes):
                raise DataLocationArtifactInvalid("location identity/owner is immutable")
            by_id[location_id].update(changes)
        seen.remove(path)
        return effective

    @staticmethod
    def _validate_registration(item: DataLocationRegistration) -> None:
        required = (
            item.location_id, item.owner, item.schema_version,
            item.retention_class,
        )
        if any(not value.strip() for value in required):
            raise DataLocationArtifactInvalid("location fields must not be blank")
        if not item.allowed_producers:
            raise DataLocationArtifactInvalid(
                f"location has no bounded producers: {item.location_id}"
            )
        if item.readiness is LocationReadiness.WRITE_APPROVED and not all((
            item.delete_adapter_id,
            item.proof_contract_id,
            item.restore_fence_id,
        )):
            raise DataLocationArtifactInvalid(
                f"write-approved location lacks deletion/proof/restore contract: "
                f"{item.location_id}"
            )

    def get(self, location_id: str) -> DataLocationRegistration:
        try:
            return self._by_id[location_id]
        except KeyError as exc:
            raise UnknownDataLocation(location_id) from exc

    def authorize_contract(self, intent: DataWriteIntent) -> DataLocationRegistration:
        location = self.get(intent.location_id)
        if location.readiness is not LocationReadiness.WRITE_APPROVED:
            raise DataLocationWriteDenied(
                f"location is registered but not write-approved: {intent.location_id}"
            )
        if intent.producer not in location.allowed_producers:
            raise DataLocationWriteDenied(
                f"producer is not approved for location: {intent.producer}"
            )
        if intent.schema_version != location.schema_version:
            raise DataLocationWriteDenied("location schema version mismatch")
        if intent.retention_class != location.retention_class:
            raise DataLocationWriteDenied("location retention class mismatch")
        if not all((
            location.delete_adapter_id,
            location.proof_contract_id,
            location.restore_fence_id,
        )):
            raise DataLocationWriteDenied(
                "delete adapter, proof and restore fence are required"
            )
        return location

    def artifact_summary(self) -> Mapping[str, Any]:
        return {
            "schema": self.schema,
            "version": self.version,
            "approved_by": self.approved_by,
            "fingerprint": self.fingerprint,
            "location_count": len(self.registrations),
            "write_approved_count": sum(
                item.readiness is LocationReadiness.WRITE_APPROVED
                for item in self.registrations
            ),
        }


def default_registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "governance" / "data_locations" / "v3.json"


def _optional(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
