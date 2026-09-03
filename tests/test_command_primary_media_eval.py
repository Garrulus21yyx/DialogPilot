from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

from application.media_asset import AssetAdmissionPolicy, AssetStatus
from application.media_evidence import (
    CoordinateSpace,
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
from application.perception import PerceptionArtifact, TieredPerceptionService
from application.routing_media_probe import (
    RoutingAssetRef,
    RoutingMediaContext,
    RoutingMediaNeed,
    RoutingMediaProbe,
    RoutingMediaScope,
)
from evaluation.command_primary_eval.contracts import EvalCase, EvaluationStatus
from evaluation.command_primary_eval.media import (
    MediaArtifactObservation,
    MediaConsumption,
    MediaDirectAdapter,
    MediaDirectRequest,
)
from evaluation.command_primary_eval.media_runner import MediaDirectRunner


PNG = b"\x89PNG\r\n\x1a\nfixture"
NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def test_media_direct_runner_records_probe_perception_and_consumption(tmp_path):
    admitted = AssetAdmissionPolicy().admit(
        tenant_id="tenant-a",
        user_id="user-a",
        turn_key="turn-a",
        filename="screen.png",
        declared_media_type="image/png",
        content=PNG,
    )
    asset = replace(admitted, status=AssetStatus.SCANNED)
    routing_asset = RoutingAssetRef(asset.asset_id, asset.checksum, "turn-a")
    binding = MediaRequirementBinding.create(
        requirement_id="media.error_code",
        asset_id=asset.asset_id,
        necessity=MediaNecessity.REQUIRED,
        required_stage=MediaStage.L1_TEXT_EXTRACTION,
        reason_code="TEXT_EXTRACTION_REQUIRED",
    )
    decision = MediaRequirementDecision.create(
        mode=MediaRequirementMode.MEDIA_TARGETS,
        bindings=(binding,),
        decision_reason_codes=("TEXT_EXTRACTION_REQUIRED",),
        task_schema_hash="a" * 64,
    )
    request = MediaDirectRequest(
        need=RoutingMediaNeed(
            MediaStage.L1_TEXT_EXTRACTION,
            RoutingMediaScope.CURRENT_TURN,
            asset.asset_id,
        ),
        context=RoutingMediaContext(current_assets=(routing_asset,)),
        perception_decision=decision,
        tenant_id="tenant-a",
        user_id="user-a",
    )
    case = EvalCase(
        case_id="media-routing-l1-001",
        message="message content is not used to infer a media tier",
        initial_state={"routing_media_need": "explicit-fixture"},
        expected={
            "trigger": "INVOKED",
            "probe_outcome": "L1",
            "artifact": {
                "asset_id": asset.asset_id,
                "stage": "L1_TEXT_EXTRACTION",
                "artifact_refs": ["parse-fixture"],
            },
            "consumed_artifact_refs": ["parse-fixture"],
            "outcome": "ROUTING_CONTEXT_READY",
        },
        slice="media_l1",
    )

    class Assets:
        def get(self, asset_id, *, tenant_id, user_id):
            assert asset_id == asset.asset_id
            assert (tenant_id, user_id) == ("tenant-a", "user-a")
            return asset, PNG

    class OCR:
        version = "fixture-ocr-v1"

        def extract(self, received, _content):
            locator = MediaLocator(
                received.asset_id,
                received.checksum,
                0,
                CoordinateSpace.NORMALIZED_0_1,
                (0.0, 0.0, 1.0, 1.0),
            )
            nodes = (
                ParseNode("page-0", ParseNodeKind.PAGE, locator),
                ParseNode(
                    "block-0",
                    ParseNodeKind.BLOCK,
                    locator,
                    text="E42",
                    parent_node_id="page-0",
                    confidence=1.0,
                ),
            )
            return PerceptionArtifact(
                received.asset_id,
                MediaStage.L1_TEXT_EXTRACTION,
                "fixture-ocr",
                self.version,
                parse_result=ParseResult(
                    "parse-fixture",
                    received.asset_id,
                    received.checksum,
                    ParseStatus.COMPLETE,
                    nodes,
                    "fixture-ocr",
                    "fixture-model",
                    self.version,
                    "fixture-preprocess-v1",
                    NOW,
                ),
            )

    consumed = []

    async def consumer(
        received: EvalCase,
        observation: MediaArtifactObservation,
    ) -> MediaConsumption:
        consumed.append((received.case_id, observation))
        return MediaConsumption(
            artifact_refs=observation.artifact_refs,
            outcome="ROUTING_CONTEXT_READY",
        )

    adapter = MediaDirectAdapter(
        probe=RoutingMediaProbe(),
        perception=TieredPerceptionService(Assets(), ocr=OCR(), vlm=None),
        request_loader=lambda received: request,
        consumer=consumer,
    )
    report = asyncio.run(MediaDirectRunner(adapter).run(
        (case,),
        tmp_path,
        run_id="media-run-001",
        dataset_id="synthetic-contract-v1",
        split="dev",
    ))

    assert report.status is EvaluationStatus.PASS
    assert consumed[0][0] == case.case_id
    assert consumed[0][1].artifact_refs == ("parse-fixture",)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    ]

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    prediction = json.loads((tmp_path / "predictions.jsonl").read_text())
    persisted_report = json.loads((tmp_path / "report.json").read_text())
    assert manifest["component"] == "media_routing_probe_perception"
    assert manifest["configuration"]["evaluated_scope"] == (
        "routing_probe_and_perception_artifact_only"
    )
    assert prediction["trigger"]["detail"]["actual_probe_outcome"] == "L1"
    assert prediction["artifact"]["detail"]["actual"] == case.expected["artifact"]
    assert prediction["consumption"]["passed"] is True
    assert prediction["outcome"]["detail"]["perception_statuses"] == ["SUCCEEDED"]
    assert persisted_report["dimensions"]["cost"]["total_invocations"] == 1
