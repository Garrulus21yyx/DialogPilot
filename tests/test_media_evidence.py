"""Media locator, ParseResult, EvidenceNode and cache-key contracts."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from application.media_evidence import (
    CoordinateSpace,
    EvidenceNode,
    MediaEvidenceContractError,
    MediaLocator,
    ParseNode,
    ParseNodeKind,
    ParseResult,
    ParseStatus,
    PerceptionCacheKey,
)
from application.media_requirement import MediaStage


SHA = "a" * 64
NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _locator(**overrides):
    values = {
        "asset_id": "asset:v1:one", "asset_checksum": SHA,
        "page_index": 0,
        "coordinate_space": CoordinateSpace.NORMALIZED_0_1,
        "bbox": (0.1, 0.2, 0.8, 0.9),
    }
    values.update(overrides)
    return MediaLocator(**values)


def test_locator_requires_zero_based_page_bounded_bbox_and_reversible_crop():
    assert _locator().page_index == 0
    with pytest.raises(MediaEvidenceContractError, match="zero-based"):
        _locator(page_index=-1)
    with pytest.raises(MediaEvidenceContractError, match="bounds"):
        _locator(bbox=(0.1, 0.2, 1.1, 0.9))
    with pytest.raises(MediaEvidenceContractError, match="reversible"):
        _locator(crop_artifact_id="crop-1")
    crop = _locator(
        crop_artifact_id="crop-1", crop_transform_version="crop-affine-v1",
    )
    assert crop.crop_transform_version == "crop-affine-v1"


def test_parse_result_preserves_page_structure_and_asset_provenance():
    page = ParseNode("page-0", ParseNodeKind.PAGE, _locator())
    block = ParseNode(
        "block-1", ParseNodeKind.BLOCK, _locator(bbox=(0.1, 0.2, 0.5, 0.3)),
        text="错误代码 E42", parent_node_id="page-0", confidence=0.98,
    )
    result = ParseResult(
        "parse-1", "asset:v1:one", SHA, ParseStatus.COMPLETE,
        (page, block), "local-ocr", "deterministic-fixture",
        "local-ocr-v1", "image-normalize-v1", NOW,
    )
    assert result.nodes[1].parent_node_id == "page-0"
    with pytest.raises(MediaEvidenceContractError, match="parent"):
        replace(result, nodes=(replace(block, parent_node_id="missing"),))
    with pytest.raises(MediaEvidenceContractError, match="provenance drift"):
        replace(result, nodes=(page, replace(block, locator=_locator(
            asset_id="asset:v1:other",
        ))))


@pytest.mark.parametrize("status", [
    ParseStatus.PARTIAL, ParseStatus.FAILED, ParseStatus.CONFLICT,
])
def test_non_complete_parse_results_require_typed_errors(status):
    nodes = () if status is ParseStatus.FAILED else (
        ParseNode("page-0", ParseNodeKind.PAGE, _locator()),
    )
    with pytest.raises(MediaEvidenceContractError, match="typed errors"):
        ParseResult(
            "parse-1", "asset:v1:one", SHA, status, nodes,
            "local-ocr", "fixture", "v1", "pre-v1", NOW,
        )


def test_evidence_node_hash_covers_locator_value_and_producer():
    node = EvidenceNode.create(
        observation_type="error_code", value={"text": "E42"},
        locator=_locator(), confidence=0.9, producer_model="fixture-ocr",
        producer_version="fixture-ocr-v1", created_at=NOW,
    )
    assert node.evidence_id.endswith(node.content_hash)
    with pytest.raises(MediaEvidenceContractError, match="hash drift"):
        replace(node, value={"text": "E43"})


def test_task_conditioned_l2_cache_requires_both_task_fingerprints():
    common = {
        "tenant_scope": "tenant-a", "locator": _locator(),
        "media_need": MediaStage.L2_VISUAL_REASONING,
        "preprocessing_version": "pre-v1", "producer": "vlm",
        "producer_model": "vision-model", "producer_version": "vlm-v1",
    }
    with pytest.raises(MediaEvidenceContractError, match="both task"):
        PerceptionCacheKey(**common)
    key = PerceptionCacheKey(
        **common, task_schema_hash="b" * 64,
        normalized_task_input_hash="c" * 64,
    )
    assert len(key.fingerprint) == 64
    assert key.fingerprint != replace(
        key, producer_version="vlm-v2",
    ).fingerprint


def test_generic_l1_cache_does_not_require_task_fingerprints():
    key = PerceptionCacheKey(
        tenant_scope="tenant-a", locator=_locator(),
        media_need=MediaStage.L1_TEXT_EXTRACTION,
        preprocessing_version="pre-v1", producer="ocr",
        producer_model="local-ocr", producer_version="ocr-v1",
    )
    assert len(key.fingerprint) == 64
