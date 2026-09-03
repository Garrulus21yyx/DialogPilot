"""One locked L0 turn proves transport, not benchmark quality."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from application.chat_application import Completed, StageStatus
from evaluation.command_primary_eval.locked_conversation import (
    LockedConversationTransportAdapter,
)
from tests.support.command_primary_clarify_harness import (
    build_command_primary_clarify_harness,
)


DATASET = Path("data/eval/dialogpilot-synthetic-contract-v1")


def test_locked_l0_case_runs_as_non_scoring_clarify_transport_smoke() -> None:
    harness = build_command_primary_clarify_harness()
    adapter = LockedConversationTransportAdapter(harness.application, DATASET)

    turns = asyncio.run(adapter.run("dp-product-09-a"))

    assert len(turns) == 1
    assert turns[0].status == "TRANSPORT_SMOKE"
    assert turns[0].score_eligible is False
    assert isinstance(turns[0].outcome, Completed)
    response = turns[0].outcome.response
    assert response["routing_disposition"] == "clarify"
    assert response["response"] == "请补充你希望处理的具体对象或必要信息，我再继续。"
    assert response["coverage"] == {
        "complete": False,
        "missing_inputs": ["request_goal"],
    }
    assert response["pending_signals"][0]["missing_inputs"] == (
        "request_goal",
    )
    assert response["tool_audit"] == []
    assert harness.published == [response["response"]]
    assert harness.persisted[-1] == response["response"]
    assert harness.semantic.messages == ["帮我看看这个是不是我需要的耳机麦克风？"]

    stages = {stage.stage: stage for stage in turns[0].outcome.stages}
    assert stages["intent"].status is StageStatus.SKIPPED
    assert stages["media"].detail["reason"] == "WORK_PLAN_FORBIDS_MEDIA"
    assert stages["knowledge_retrieval"].detail["used"] is False
    assert "missing_input_signal" in (
        stages["route_path_plan"].detail["required_components"]
    )

    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_status"] == "NOT_RUN"
    assert manifest["promotion_allowed"] is False
