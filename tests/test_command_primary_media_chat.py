"""One real ChatApplication path for command-primary L1 media work."""
from __future__ import annotations

import asyncio

from application.chat_application import Completed, StageStatus
from application.default_flow_registry import MEDIA_TEXT_READ
from tests.support.command_primary_media_harness import (
    build_command_primary_media_harness,
)


def test_command_primary_l1_media_is_consumed_and_published() -> None:
    harness = build_command_primary_media_harness()

    outcome = asyncio.run(harness.application.handle(harness.command))

    assert isinstance(outcome, Completed)
    assert harness.media_agent.calls == 1
    assert harness.media_agent.task == {
        "media_need": "L1_TEXT_EXTRACTION",
        "requirement_id": "media.visible_text",
    }
    assert harness.ocr.calls == 1
    assert outcome.response["response"] == "识别到的文字：E401"
    assert outcome.response["intent_prediction_id"] == ""
    assert outcome.response["tool_audit"] == []
    assert outcome.response["coverage"]["media_artifact_refs"] == [
        "parse-media-l1-e2e",
    ]
    assert outcome.response["media"]["ocr_invoked"] is True
    assert outcome.response["media"]["vlm_invoked"] is False
    assert harness.persisted_messages[-1] == "识别到的文字：E401"

    current = harness.flow_state.current
    assert current is not None
    assert current.aggregate.version == 1
    assert current.active_flows[0].definition == MEDIA_TEXT_READ
    assert current.active_flows[0].state_version == 1

    stages = {stage.stage: stage for stage in outcome.stages}
    assert stages["intent"].status is StageStatus.SKIPPED
    assert stages["media"].status is StageStatus.OK
    assert stages["flow_transition"].status is StageStatus.OK
    assert stages["tool"].detail["call_count"] == 0
    assert stages["route_path_plan"].detail["candidate_owner"] == "media_evidence"
    assert "media_perception" in (
        stages["route_path_plan"].detail["required_components"]
    )
    assert "business_tool" in (
        stages["route_path_plan"].detail["forbidden_components"]
    )
