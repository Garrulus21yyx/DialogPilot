"""Demand-driven OCR/VLM execution over validated Agent media decisions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from application.media_asset import AssetAdmission, AssetStatus, MediaAssetError
from application.media_evidence import EvidenceNode, ParseResult
from application.media_requirement import (
    MediaRequirementDecision,
    MediaRequirementMode,
    MediaStage,
)


class PerceptionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class PerceptionArtifact:
    asset_id: str
    stage: MediaStage
    producer: str
    producer_version: str
    parse_result: ParseResult | None = None
    evidence_nodes: tuple[EvidenceNode, ...] = ()

    def __post_init__(self) -> None:
        if not all((
            self.asset_id.strip(), self.producer.strip(),
            self.producer_version.strip(),
        )):
            raise ValueError("perception artifact provenance is incomplete")
        if self.stage is MediaStage.L1_TEXT_EXTRACTION and self.parse_result is None:
            raise ValueError("L1 artifact requires ParseResult")
        if self.stage is MediaStage.L2_VISUAL_REASONING and not self.evidence_nodes:
            raise ValueError("L2 artifact requires EvidenceNode")


@dataclass(frozen=True)
class PerceptionOutcome:
    media_binding_id: str
    asset_id: str
    required_stage: MediaStage
    status: PerceptionStatus
    artifact_refs: tuple[str, ...] = ()
    reason_code: str = ""


class OCRProviderPort(Protocol):
    version: str

    def extract(
        self, asset: AssetAdmission, content: bytes,
    ) -> PerceptionArtifact: ...


class VLMProviderPort(Protocol):
    version: str

    def observe(
        self,
        asset: AssetAdmission,
        content: bytes,
        *,
        region_key: str | None,
        requirement_id: str,
        task_schema_hash: str,
    ) -> PerceptionArtifact: ...


class MediaAssetReadPort(Protocol):
    def get(
        self, asset_id: str, *, tenant_id: str, user_id: str,
    ) -> tuple[AssetAdmission, bytes]: ...


class TieredPerceptionService:
    """Execute exactly the stages fixed by a validated Agent decision."""

    def __init__(
        self,
        assets: MediaAssetReadPort,
        *,
        ocr: OCRProviderPort | None,
        vlm: VLMProviderPort | None,
    ):
        self.assets = assets
        self.ocr = ocr
        self.vlm = vlm

    def execute(
        self,
        decision: MediaRequirementDecision,
        *,
        tenant_id: str,
        user_id: str,
    ) -> tuple[PerceptionOutcome, ...]:
        if decision.mode is MediaRequirementMode.NO_MEDIA_REQUIRED:
            if decision.bindings:
                raise ValueError("NO_MEDIA_REQUIRED cannot execute bindings")
            return ()
        outcomes = []
        ocr_refs: dict[str, str] = {}
        for binding in decision.bindings:
            try:
                asset, content = self.assets.get(
                    binding.asset_id, tenant_id=tenant_id, user_id=user_id,
                )
                if asset.status is not AssetStatus.SCANNED:
                    raise MediaAssetError(
                        "ASSET_NOT_READY", "asset has not passed security scan",
                    )
                refs = []
                if binding.required_stage >= MediaStage.L1_TEXT_EXTRACTION:
                    if self.ocr is None:
                        outcomes.append(PerceptionOutcome(
                            binding.media_binding_id, binding.asset_id,
                            binding.required_stage, PerceptionStatus.UNAVAILABLE,
                            reason_code="OCR_PROVIDER_UNAVAILABLE",
                        ))
                        continue
                    if binding.asset_id not in ocr_refs:
                        artifact = self.ocr.extract(asset, content)
                        self._validate_artifact(
                            artifact, asset, MediaStage.L1_TEXT_EXTRACTION,
                        )
                        ocr_refs[binding.asset_id] = _artifact_ref(artifact)
                    refs.append(ocr_refs[binding.asset_id])
                if binding.required_stage is MediaStage.L2_VISUAL_REASONING:
                    if self.vlm is None:
                        outcomes.append(PerceptionOutcome(
                            binding.media_binding_id, binding.asset_id,
                            binding.required_stage, PerceptionStatus.UNAVAILABLE,
                            tuple(refs), "VLM_PROVIDER_UNAVAILABLE",
                        ))
                        continue
                    artifact = self.vlm.observe(
                        asset, content, region_key=binding.region_key,
                        requirement_id=binding.requirement_id,
                        task_schema_hash=decision.task_schema_hash,
                    )
                    self._validate_artifact(
                        artifact, asset, MediaStage.L2_VISUAL_REASONING,
                    )
                    refs.append(_artifact_ref(artifact))
                outcomes.append(PerceptionOutcome(
                    binding.media_binding_id, binding.asset_id,
                    binding.required_stage, PerceptionStatus.SUCCEEDED,
                    tuple(refs), "PERCEPTION_COMPLETE",
                ))
            except MediaAssetError as exc:
                outcomes.append(PerceptionOutcome(
                    binding.media_binding_id, binding.asset_id,
                    binding.required_stage, PerceptionStatus.INVALID_CONTRACT,
                    reason_code=exc.code,
                ))
            except Exception as exc:
                outcomes.append(PerceptionOutcome(
                    binding.media_binding_id, binding.asset_id,
                    binding.required_stage, PerceptionStatus.FAILED,
                    reason_code=type(exc).__name__,
                ))
        return tuple(outcomes)

    @staticmethod
    def _validate_artifact(
        artifact: PerceptionArtifact,
        asset: AssetAdmission,
        expected_stage: MediaStage,
    ) -> None:
        if artifact.asset_id != asset.asset_id or artifact.stage is not expected_stage:
            raise ValueError("perception artifact identity/stage drift")
        if artifact.parse_result is not None and (
            artifact.parse_result.asset_id != asset.asset_id
            or artifact.parse_result.asset_checksum != asset.checksum
        ):
            raise ValueError("ParseResult asset provenance drift")
        if any(
            node.locator.asset_id != asset.asset_id
            or node.locator.asset_checksum != asset.checksum
            for node in artifact.evidence_nodes
        ):
            raise ValueError("EvidenceNode asset provenance drift")


def _artifact_ref(artifact: PerceptionArtifact) -> str:
    if artifact.parse_result is not None:
        return artifact.parse_result.parse_result_id
    return artifact.evidence_nodes[0].evidence_id
