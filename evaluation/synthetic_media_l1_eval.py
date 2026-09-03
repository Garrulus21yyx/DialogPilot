"""Project-only L1 diagnostic assembled from one synthetic media fixture."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from application.media_requirement import (
    MediaNecessity,
    MediaRequirementBinding,
    MediaRequirementDecision,
    MediaRequirementMode,
    MediaStage,
)
from application.routing_media_probe import (
    RoutingAssetRef,
    RoutingMediaContext,
    RoutingMediaNeed,
    RoutingMediaScope,
)
from evaluation.command_primary_eval.contracts import EvalCase
from evaluation.command_primary_eval.media import (
    MediaArtifactObservation,
    MediaConsumption,
    MediaDirectRequest,
)
from evaluation.synthetic_media_fixture import (
    SingleAssetStore,
    SyntheticMediaFixture,
    load_synthetic_media_fixture,
)
from infrastructure.tesseract_ocr_provider import TesseractOCRProvider


@dataclass(frozen=True)
class SyntheticMediaL1Bundle:
    case: EvalCase
    request: MediaDirectRequest
    assets: SingleAssetStore
    fixture: SyntheticMediaFixture


def load_synthetic_media_l1(
    dataset_root: str | Path,
    *,
    source_case_id: str,
    language: str,
) -> SyntheticMediaL1Bundle:
    fixture = load_synthetic_media_fixture(
        dataset_root,
        source_case_id=source_case_id,
    )
    asset = fixture.asset
    output_locator = TesseractOCRProvider.output_locator(
        asset,
        locator=fixture.locator,
        width=fixture.width,
        height=fixture.height,
    )
    parse_ref = TesseractOCRProvider.parse_result_id(
        asset,
        language=language,
        width=fixture.width,
        height=fixture.height,
        locator=output_locator,
    )
    page_locator = TesseractOCRProvider.output_locator(
        asset,
        locator=None,
        width=fixture.width,
        height=fixture.height,
    )
    eval_case = EvalCase(
        case_id=f"{source_case_id}:region-l1-diagnostic",
        message=fixture.message,
        initial_state={
            "source_case_id": source_case_id,
            "source_annotation_ref": fixture.annotation_ref,
            "evaluation_scope": "EXPLICIT_REGION_L1_ONLY",
        },
        expected={
            "trigger": "INVOKED",
            "probe_outcome": "L1",
            "artifact": {
                "asset_id": asset.asset_id,
                "stage": "L1_TEXT_EXTRACTION",
                "artifact_refs": [parse_ref],
                "grounding": [
                    {
                        "artifact_ref": parse_ref,
                        "asset_id": asset.asset_id,
                        "asset_checksum": asset.checksum,
                        "stage": "L1_TEXT_EXTRACTION",
                        "status": "COMPLETE",
                        "producer": "tesseract",
                        "producer_model": f"tesseract-{language}",
                        "producer_version": TesseractOCRProvider.version,
                        "preprocessing_version": (
                            TesseractOCRProvider.crop_transform_version
                        ),
                        "locators": [
                            page_locator.to_dict(),
                            output_locator.to_dict(),
                        ],
                    }
                ],
            },
            "consumed_artifact_refs": [parse_ref],
            "outcome": "GROUNDED_TEXT_MATCH",
        },
        slice="synthetic_media_l1_project_diagnostic",
    )
    binding = MediaRequirementBinding.create(
        requirement_id="media.asset_text",
        asset_id=asset.asset_id,
        necessity=MediaNecessity.REQUIRED,
        required_stage=MediaStage.L1_TEXT_EXTRACTION,
        reason_code="TEXT_EXTRACTION_REQUIRED",
        region_key=fixture.region_ref,
    )
    request = MediaDirectRequest(
        need=RoutingMediaNeed(
            MediaStage.L1_TEXT_EXTRACTION,
            RoutingMediaScope.CURRENT_TURN,
            asset.asset_id,
        ),
        context=RoutingMediaContext(
            current_assets=(
                RoutingAssetRef(
                    asset.asset_id,
                    asset.checksum,
                    fixture.turn_id,
                ),
            )
        ),
        perception_decision=MediaRequirementDecision.create(
            mode=MediaRequirementMode.MEDIA_TARGETS,
            bindings=(binding,),
            decision_reason_codes=("TEXT_EXTRACTION_REQUIRED",),
            task_schema_hash=hashlib.sha256(
                b"synthetic-media-region-text-v1"
            ).hexdigest(),
        ),
        tenant_id=fixture.tenant_id,
        user_id=fixture.user_id,
    )
    return SyntheticMediaL1Bundle(
        eval_case,
        request,
        SingleAssetStore(fixture),
        fixture,
    )


async def consume_expected_text(
    bundle: SyntheticMediaL1Bundle,
    _case: EvalCase,
    observation: MediaArtifactObservation,
) -> MediaConsumption:
    text = "\n".join(
        node.text
        for artifact in observation.artifacts
        if artifact.parse_result is not None
        for node in artifact.parse_result.nodes
        if node.text
    )
    normalized = _normalize(text)
    missing = tuple(
        fragment
        for fragment in bundle.fixture.expected_fragments
        if _normalize(fragment) not in normalized
    )
    return MediaConsumption(
        artifact_refs=observation.artifact_refs,
        outcome="GROUNDED_TEXT_MATCH" if not missing else "GROUNDED_TEXT_MISMATCH",
        detail={
            "annotation_ref": bundle.fixture.annotation_ref,
            "expected_fragments": list(bundle.fixture.expected_fragments),
            "missing_fragments": list(missing),
            "observed_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        },
    )


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9_-]+", " ", text.casefold()).split())
