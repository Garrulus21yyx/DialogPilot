"""Positive L1 media transport through ``ChatApplication.handle``."""
from __future__ import annotations

import asyncio

from application.chat_application import Completed, StageStatus
from tests.support.media_l1_chat_harness import build_media_l1_chat_harness


def test_l1_media_is_consumed_and_published_through_chat_application() -> None:
    harness = build_media_l1_chat_harness()

    outcome = asyncio.run(harness.application.handle(harness.command))

    assert isinstance(outcome, Completed)
    assert harness.decision_producer.calls == 1
    assert harness.ocr.calls == 1
    assert harness.worker.consumed_text == "E401"
    assert harness.tools.audit_records() == []
    assert harness.persisted_messages[-1] == "页面显示错误码 E401。"
    assert outcome.response["response"] == "页面显示错误码 E401。"
    assert outcome.response["tool_audit"] == []
    assert outcome.response["media"] == {
        "mode": "MEDIA_TARGETS",
        "decision_id": outcome.response["media"]["decision_id"],
        "asset_ids": list(harness.command.asset_ids),
        "ocr_invoked": True,
        "vlm_invoked": False,
        "outcomes": [{
            "asset_id": harness.command.asset_ids[0],
            "required_stage": "L1_TEXT_EXTRACTION",
            "status": "SUCCEEDED",
            "reason_code": "PERCEPTION_COMPLETE",
            "artifact_refs": ["parse-media-l1-e2e"],
        }],
        "producers": [{
            "stage": "L1_TEXT_EXTRACTION",
            "producer": "fixture-ocr",
            "producer_version": "fixture-ocr-v1",
            "model": "fixture-model",
        }],
    }
    stages = {stage.stage: stage for stage in outcome.stages}
    assert stages["media"].status is StageStatus.OK
    assert stages["media"].detail["observation_count"] == 1
    assert stages["tool"].detail["call_count"] == 0
