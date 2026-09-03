"""Validated access to one L1 case in the locked synthetic media corpus."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from application.media_asset import (
    AssetAdmission,
    AssetAdmissionPolicy,
    AssetStatus,
)


class SyntheticMediaFixtureError(ValueError):
    pass


@dataclass(frozen=True)
class SyntheticMediaFixture:
    source_case_id: str
    message: str
    turn_id: str
    tenant_id: str
    user_id: str
    asset: AssetAdmission
    content: bytes
    width: int
    height: int
    annotation_ref: str
    expected_fragments: tuple[str, ...]


class SingleAssetStore:
    """Read one admitted fixture through the production perception port."""

    def __init__(self, fixture: SyntheticMediaFixture) -> None:
        self._fixture = fixture

    def get(
        self,
        asset_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> tuple[AssetAdmission, bytes]:
        fixture = self._fixture
        if asset_id != fixture.asset.asset_id:
            raise SyntheticMediaFixtureError("fixture asset identity mismatch")
        if (tenant_id, user_id) != (fixture.tenant_id, fixture.user_id):
            raise SyntheticMediaFixtureError("fixture asset principal mismatch")
        return fixture.asset, fixture.content


def load_synthetic_media_fixture(
    dataset_root: str | Path,
    *,
    source_case_id: str,
) -> SyntheticMediaFixture:
    root = Path(dataset_root)
    manifest = _read_json(root / "manifest.json")
    catalog = _read_json(root / "synthetic-fixtures.json")
    _verify_dataset_identity(manifest)
    _verify_locked_file(root, manifest, "cases.jsonl")
    _verify_locked_file(root, manifest, "synthetic-fixtures.json")

    source = _load_case(root / "cases.jsonl", source_case_id)
    turn = _one(source.get("turns"), "source turn")
    media = dict(turn.get("expected", {}).get("media") or {})
    if media.get("media_need") != "L1":
        raise SyntheticMediaFixtureError("source case is not an explicit L1 case")
    asset_ref = str(_one(media.get("required_asset_refs"), "asset ref"))
    annotation_ref = str(
        _one(source.get("provenance", {}).get("annotation_refs"), "annotation ref")
    )
    asset_spec = dict(catalog.get("assets", {}).get(asset_ref) or {})
    annotation = dict(catalog.get("annotations", {}).get(annotation_ref) or {})
    if not asset_spec or annotation.get("asset_ref") != asset_ref:
        raise SyntheticMediaFixtureError("fixture annotation is not bound to the asset")

    relative_path = str(asset_spec.get("path") or "")
    _verify_locked_file(root, manifest, relative_path)
    content = (root / relative_path).read_bytes()
    initial = dict(source.get("initial_state") or {})
    tenant_id = str(initial.get("tenant_id") or "")
    user_id = str(initial.get("user_id") or "")
    turn_id = str(turn.get("turn_id") or "")
    asset = AssetAdmissionPolicy().admit(
        tenant_id=tenant_id,
        user_id=user_id,
        turn_key=turn_id,
        filename=Path(relative_path).name,
        declared_media_type=str(asset_spec.get("media_type") or ""),
        content=content,
    )
    asset = replace(asset, status=AssetStatus.SCANNED)
    if asset.checksum != str(asset_spec.get("sha256") or ""):
        raise SyntheticMediaFixtureError("admitted asset checksum drift")
    fragments = _ascii_fragments(str(annotation.get("allowed_observation") or ""))
    if not fragments:
        raise SyntheticMediaFixtureError("annotation has no English OCR fragment")
    return SyntheticMediaFixture(
        source_case_id=source_case_id,
        message=str(turn.get("user_message") or ""),
        turn_id=turn_id,
        tenant_id=tenant_id,
        user_id=user_id,
        asset=asset,
        content=content,
        width=int(asset_spec["width"]),
        height=int(asset_spec["height"]),
        annotation_ref=annotation_ref,
        expected_fragments=fragments,
    )


def _verify_dataset_identity(manifest: Mapping[str, Any]) -> None:
    if not all(
        (
            manifest.get("dataset_id") == "dialogpilot-synthetic-contract-v1",
            manifest.get("dataset_role") == "ARCHITECTURE_CONTRACT",
            manifest.get("lock_status") == "SYNTHETIC_CONTRACT_LOCKED",
        )
    ):
        raise SyntheticMediaFixtureError("unsupported synthetic fixture dataset")


def _load_case(path: Path, case_id: str) -> Mapping[str, Any]:
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if value.get("case_id") == case_id:
            if value.get("status") != "SYNTHETIC_CONTRACT_LOCKED":
                raise SyntheticMediaFixtureError("source case is not locked")
            return value
    raise SyntheticMediaFixtureError(f"unknown source case: {case_id}")


def _verify_locked_file(
    root: Path,
    manifest: Mapping[str, Any],
    relative_path: str,
) -> None:
    expected = dict(manifest.get("locked_file_sha256") or {}).get(relative_path)
    path = root / relative_path
    if not expected or not path.is_file():
        raise SyntheticMediaFixtureError(f"unlocked fixture file: {relative_path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SyntheticMediaFixtureError(f"fixture checksum mismatch: {relative_path}")


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _one(values: Any, name: str) -> Any:
    if not isinstance(values, list) or len(values) != 1:
        raise SyntheticMediaFixtureError(f"exactly one {name} is required")
    return values[0]


def _ascii_fragments(text: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in text.split("；")
        if item.strip() and item.strip().isascii()
    )
