"""L0/L1/L2 invocation and typed-failure properties."""
from datetime import datetime, timezone

from application.media_asset import (
    AssetAdmissionPolicy,
    AssetStatus,
)
from application.media_evidence import (
    CoordinateSpace,
    EvidenceNode,
    MediaLocator,
    ParseNode,
    ParseNodeKind,
    ParseResult,
    ParseStatus,
)
from application.media_requirement import (
    MediaNecessity,
    MediaRequirementBinding,
    MediaRequirementDecision,
    MediaRequirementMode,
    MediaStage,
)
from application.perception import (
    PerceptionArtifact,
    PerceptionStatus,
    TieredPerceptionService,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"
NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _asset():
    admitted = AssetAdmissionPolicy().admit(
        tenant_id="tenant-a", user_id="user-a", turn_key="turn-a",
        filename="screen.png", declared_media_type="image/png", content=PNG,
    )
    return admitted.__class__(**{
        **admitted.__dict__, "status": AssetStatus.SCANNED,
    })


class Assets:
    def __init__(self, asset=None):
        self.asset = asset or _asset()

    def get(self, asset_id, *, tenant_id, user_id):
        assert asset_id == self.asset.asset_id
        assert (tenant_id, user_id) == ("tenant-a", "user-a")
        return self.asset, PNG


def _locator(asset):
    return MediaLocator(
        asset.asset_id, asset.checksum, 0,
        CoordinateSpace.NORMALIZED_0_1, (0.0, 0.0, 1.0, 1.0),
    )


class OCR:
    version = "fixture-ocr-v1"

    def __init__(self):
        self.calls = 0

    def extract(self, asset, _content):
        self.calls += 1
        page = ParseNode("page-0", ParseNodeKind.PAGE, _locator(asset))
        block = ParseNode(
            "block-0", ParseNodeKind.BLOCK, _locator(asset), text="E42",
            parent_node_id="page-0", confidence=1.0,
        )
        return PerceptionArtifact(
            asset.asset_id, MediaStage.L1_TEXT_EXTRACTION,
            "fixture-ocr", self.version,
            parse_result=ParseResult(
                "parse-fixture", asset.asset_id, asset.checksum,
                ParseStatus.COMPLETE, (page, block), "fixture-ocr",
                "fixture", self.version, "pre-v1", NOW,
            ),
        )


class VLM:
    version = "fixture-vlm-v1"

    def __init__(self):
        self.calls = []

    def observe(
        self, asset, _content, *, region_key, requirement_id, task_schema_hash,
    ):
        self.calls.append((region_key, requirement_id, task_schema_hash))
        node = EvidenceNode.create(
            observation_type="visual_state", value={"state": "button_visible"},
            locator=_locator(asset), confidence=0.9,
            producer_model="fixture-vlm", producer_version=self.version,
            created_at=NOW,
        )
        return PerceptionArtifact(
            asset.asset_id, MediaStage.L2_VISUAL_REASONING,
            "fixture-vlm", self.version, evidence_nodes=(node,),
        )


def _decision(stage, *, count=1):
    asset = _asset()
    bindings = tuple(MediaRequirementBinding.create(
        requirement_id=f"media.screen.{index}", asset_id=asset.asset_id,
        necessity=MediaNecessity.REQUIRED, required_stage=stage,
        reason_code=(
            "TEXT_EXTRACTION_REQUIRED"
            if stage is MediaStage.L1_TEXT_EXTRACTION
            else "VISUAL_EVIDENCE_REQUIRED"
        ),
        region_key=f"region-{index}" if stage is MediaStage.L2_VISUAL_REASONING else None,
    ) for index in range(count))
    return MediaRequirementDecision.create(
        mode=MediaRequirementMode.MEDIA_TARGETS, bindings=bindings,
        decision_reason_codes=(bindings[0].reason_code,),
        task_schema_hash="a" * 64,
    )


def test_l0_no_media_executes_zero_providers():
    ocr, vlm = OCR(), VLM()
    decision = MediaRequirementDecision.create(
        mode=MediaRequirementMode.NO_MEDIA_REQUIRED, bindings=(),
        decision_reason_codes=("MEDIA_IRRELEVANT",),
        task_schema_hash="a" * 64,
    )
    outcomes = TieredPerceptionService(
        Assets(), ocr=ocr, vlm=vlm,
    ).execute(decision, tenant_id="tenant-a", user_id="user-a")
    assert outcomes == ()
    assert ocr.calls == 0
    assert vlm.calls == []


def test_l1_runs_ocr_only():
    ocr, vlm = OCR(), VLM()
    outcomes = TieredPerceptionService(
        Assets(), ocr=ocr, vlm=vlm,
    ).execute(
        _decision(MediaStage.L1_TEXT_EXTRACTION),
        tenant_id="tenant-a", user_id="user-a",
    )
    assert outcomes[0].status is PerceptionStatus.SUCCEEDED
    assert ocr.calls == 1
    assert vlm.calls == []


def test_execution_batch_returns_exact_artifacts_for_context_building():
    batch = TieredPerceptionService(
        Assets(), ocr=OCR(), vlm=None,
    ).execute_with_artifacts(
        _decision(MediaStage.L1_TEXT_EXTRACTION),
        tenant_id="tenant-a", user_id="user-a",
    )

    assert batch.outcomes[0].status is PerceptionStatus.SUCCEEDED
    assert len(batch.artifacts) == 1
    assert batch.artifacts[0].parse_result.parse_result_id == "parse-fixture"


def test_l2_runs_ocr_once_then_vlm_for_each_explicit_binding():
    ocr, vlm = OCR(), VLM()
    outcomes = TieredPerceptionService(
        Assets(), ocr=ocr, vlm=vlm,
    ).execute(
        _decision(MediaStage.L2_VISUAL_REASONING, count=2),
        tenant_id="tenant-a", user_id="user-a",
    )
    assert [item.status for item in outcomes] == [
        PerceptionStatus.SUCCEEDED, PerceptionStatus.SUCCEEDED,
    ]
    assert ocr.calls == 1
    assert len(vlm.calls) == 2
    assert [call[0] for call in vlm.calls] == ["region-0", "region-1"]


def test_missing_vlm_is_typed_unavailable_and_keeps_ocr_ref():
    ocr = OCR()
    outcome = TieredPerceptionService(
        Assets(), ocr=ocr, vlm=None,
    ).execute(
        _decision(MediaStage.L2_VISUAL_REASONING),
        tenant_id="tenant-a", user_id="user-a",
    )[0]
    assert outcome.status is PerceptionStatus.UNAVAILABLE
    assert outcome.reason_code == "VLM_PROVIDER_UNAVAILABLE"
    assert outcome.artifact_refs == ("parse-fixture",)


def test_quarantined_asset_is_invalid_and_invokes_no_provider():
    quarantined = _asset().__class__(**{
        **_asset().__dict__, "status": AssetStatus.QUARANTINED,
    })
    ocr, vlm = OCR(), VLM()
    outcome = TieredPerceptionService(
        Assets(quarantined), ocr=ocr, vlm=vlm,
    ).execute(
        _decision(MediaStage.L1_TEXT_EXTRACTION),
        tenant_id="tenant-a", user_id="user-a",
    )[0]
    assert outcome.status is PerceptionStatus.INVALID_CONTRACT
    assert outcome.reason_code == "ASSET_NOT_READY"
    assert ocr.calls == 0
    assert vlm.calls == []
