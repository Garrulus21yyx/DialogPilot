"""Canonical media coordinates, parse artifacts and cache identity."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from application.media_requirement import MediaStage


class MediaEvidenceContractError(ValueError):
    pass


class CoordinateSpace(str, Enum):
    ORIGINAL_PAGE_PIXELS = "ORIGINAL_PAGE_PIXELS"
    NORMALIZED_0_1 = "NORMALIZED_0_1"


class ParseNodeKind(str, Enum):
    PAGE = "PAGE"
    BLOCK = "BLOCK"
    FIGURE = "FIGURE"
    TABLE = "TABLE"
    STEP = "STEP"
    PART = "PART"


class ParseStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class MediaLocator:
    asset_id: str
    asset_checksum: str
    page_index: int
    coordinate_space: CoordinateSpace
    bbox: tuple[float, float, float, float]
    crop_artifact_id: str | None = None
    crop_transform_version: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not _is_sha256(self.asset_checksum):
            raise MediaEvidenceContractError("media asset provenance is invalid")
        if self.page_index < 0:
            raise MediaEvidenceContractError("page_index must be zero-based")
        if len(self.bbox) != 4:
            raise MediaEvidenceContractError("media bbox must have four coordinates")
        x0, y0, x1, y1 = self.bbox
        if any(not isinstance(value, (int, float)) for value in self.bbox):
            raise MediaEvidenceContractError("media bbox coordinates must be numeric")
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            raise MediaEvidenceContractError("media bbox bounds are invalid")
        if self.coordinate_space is CoordinateSpace.NORMALIZED_0_1 and (
            x1 > 1 or y1 > 1
        ):
            raise MediaEvidenceContractError("normalized bbox exceeds page bounds")
        crop_fields = (self.crop_artifact_id, self.crop_transform_version)
        if any(crop_fields) and not all(
            value is not None and str(value).strip() for value in crop_fields
        ):
            raise MediaEvidenceContractError("crop provenance must be reversible")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_checksum": self.asset_checksum,
            "page_index": self.page_index,
            "coordinate_space": self.coordinate_space.value,
            "bbox": list(self.bbox),
            "crop_artifact_id": self.crop_artifact_id,
            "crop_transform_version": self.crop_transform_version,
        }


@dataclass(frozen=True)
class ParseNode:
    node_id: str
    kind: ParseNodeKind
    locator: MediaLocator
    text: str = ""
    parent_node_id: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise MediaEvidenceContractError("parse node identity is required")
        if self.kind is ParseNodeKind.PAGE and self.parent_node_id is not None:
            raise MediaEvidenceContractError("page node cannot have a parent")
        if self.kind is not ParseNodeKind.PAGE and not self.parent_node_id:
            raise MediaEvidenceContractError("structured node requires a parent")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise MediaEvidenceContractError("parse confidence must be in [0,1]")


@dataclass(frozen=True)
class ParseResult:
    parse_result_id: str
    asset_id: str
    asset_checksum: str
    status: ParseStatus
    nodes: tuple[ParseNode, ...]
    producer: str
    producer_model: str
    producer_version: str
    preprocessing_version: str
    created_at: datetime
    errors: tuple[str, ...] = ()
    schema_version: str = "parse-result-v1"

    def __post_init__(self) -> None:
        required = (
            self.parse_result_id, self.asset_id, self.producer,
            self.producer_model, self.producer_version,
            self.preprocessing_version,
        )
        if any(not value.strip() for value in required):
            raise MediaEvidenceContractError("parse result provenance is incomplete")
        if not _is_sha256(self.asset_checksum):
            raise MediaEvidenceContractError("parse result checksum is invalid")
        if self.schema_version != "parse-result-v1":
            raise MediaEvidenceContractError("unsupported ParseResult schema")
        if self.status in {ParseStatus.COMPLETE, ParseStatus.PARTIAL} and not self.nodes:
            raise MediaEvidenceContractError("successful ParseResult requires nodes")
        if self.status is ParseStatus.COMPLETE and self.errors:
            raise MediaEvidenceContractError("complete ParseResult cannot carry errors")
        if self.status in {
            ParseStatus.PARTIAL, ParseStatus.FAILED, ParseStatus.CONFLICT,
        } and not self.errors:
            raise MediaEvidenceContractError("non-complete ParseResult requires typed errors")
        ids = {node.node_id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise MediaEvidenceContractError("duplicate parse node identity")
        for node in self.nodes:
            if (
                node.locator.asset_id != self.asset_id
                or node.locator.asset_checksum != self.asset_checksum
            ):
                raise MediaEvidenceContractError("parse node asset provenance drift")
            if node.parent_node_id is not None and node.parent_node_id not in ids:
                raise MediaEvidenceContractError("parse node parent is unavailable")
        _reject_parent_cycles(self.nodes)


@dataclass(frozen=True)
class EvidenceNode:
    evidence_id: str
    observation_type: str
    value: Mapping[str, Any]
    locator: MediaLocator
    confidence: float | None
    producer_model: str
    producer_version: str
    created_at: datetime
    content_hash: str
    schema_version: str = "media-evidence-node-v1"

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (
            self.evidence_id, self.observation_type,
            self.producer_model, self.producer_version,
        )):
            raise MediaEvidenceContractError("EvidenceNode provenance is incomplete")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise MediaEvidenceContractError("evidence confidence must be in [0,1]")
        if not _is_sha256(self.content_hash):
            raise MediaEvidenceContractError("evidence content hash is invalid")
        if self.content_hash != _hash({
            "observation_type": self.observation_type,
            "value": self.value,
            "locator": self.locator.to_dict(),
            "producer_model": self.producer_model,
            "producer_version": self.producer_version,
        }):
            raise MediaEvidenceContractError("EvidenceNode content hash drift")

    @classmethod
    def create(
        cls,
        *,
        observation_type: str,
        value: Mapping[str, Any],
        locator: MediaLocator,
        confidence: float | None,
        producer_model: str,
        producer_version: str,
        created_at: datetime,
    ) -> "EvidenceNode":
        content_hash = _hash({
            "observation_type": observation_type,
            "value": value,
            "locator": locator.to_dict(),
            "producer_model": producer_model,
            "producer_version": producer_version,
        })
        return cls(
            evidence_id=f"media-evidence:v1:{content_hash}",
            observation_type=observation_type, value=dict(value),
            locator=locator, confidence=confidence,
            producer_model=producer_model, producer_version=producer_version,
            created_at=created_at, content_hash=content_hash,
        )


@dataclass(frozen=True)
class PerceptionCacheKey:
    tenant_scope: str
    locator: MediaLocator
    media_need: MediaStage
    preprocessing_version: str
    producer: str
    producer_model: str
    producer_version: str
    task_schema_hash: str | None = None
    normalized_task_input_hash: str | None = None

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (
            self.tenant_scope, self.preprocessing_version, self.producer,
            self.producer_model, self.producer_version,
        )):
            raise MediaEvidenceContractError("perception cache provenance is incomplete")
        if self.media_need is MediaStage.L2_VISUAL_REASONING:
            if not all(_is_sha256(value or "") for value in (
                self.task_schema_hash, self.normalized_task_input_hash,
            )):
                raise MediaEvidenceContractError(
                    "task-conditioned L2 cache requires both task fingerprints"
                )

    @property
    def fingerprint(self) -> str:
        return _hash({
            "tenant_scope": self.tenant_scope,
            "locator": self.locator.to_dict(),
            "media_need": self.media_need.name,
            "preprocessing_version": self.preprocessing_version,
            "producer": self.producer,
            "producer_model": self.producer_model,
            "producer_version": self.producer_version,
            "task_schema_hash": self.task_schema_hash,
            "normalized_task_input_hash": self.normalized_task_input_hash,
        })


def _reject_parent_cycles(nodes: tuple[ParseNode, ...]) -> None:
    parents = {node.node_id: node.parent_node_id for node in nodes}
    for node_id in parents:
        seen = set()
        current = node_id
        while current is not None:
            if current in seen:
                raise MediaEvidenceContractError("parse node parent cycle")
            seen.add(current)
            current = parents.get(current)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _hash(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MediaEvidenceContractError("media evidence is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()
