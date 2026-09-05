"""One locked L0 turn proves transport, not benchmark quality."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from application.chat_contracts import Completed
from application.conversation_agent import ConversationAgent
from application.conversation_state import InMemoryConversationStateStore
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from evaluation.command_primary_eval.locked_conversation import (
    LockedConversationTransportAdapter,
)
from tests.test_target_chat_cutover import _Admission, _Publication


DATASET = Path("data/eval/dialogpilot-synthetic-contract-v1")


def test_locked_l0_case_runs_as_non_scoring_clarify_transport_smoke() -> None:
    messages = []

    class Provider:
        version = "clarify-transport-stub-v1"

        async def plan(self, payload):
            messages.append(payload["message"])
            assert "expected_outcome" not in payload
            return {
                "status": "insufficient_context",
                "missing_fields": ["asset_id"],
            }

    async def forbidden(_context):
        raise AssertionError("clarification must not execute tools or workers")

    registry = build_default_capability_registry("tenant-synthetic-contract-v1")
    runtime = OrchestrationRuntime(direct_executor=forbidden, domain_workers={})
    application = TargetChatApplication(
        manager=TargetConversationManager(
            state_store=InMemoryConversationStateStore(), registry=registry,
            understanding=CascadedTargetUnderstanding(
                StateBoundTargetUnderstanding(), ConversationAgent(Provider()),
            ),
            orchestration=runtime,
        ),
        admission=_Admission(), publication=_Publication(),
        bundle_version=registry.bundle_version,
    )
    adapter = LockedConversationTransportAdapter(application, DATASET)

    turns = asyncio.run(adapter.run("dp-product-09-a"))

    assert len(turns) == 1
    assert turns[0].status == "TRANSPORT_SMOKE"
    assert turns[0].score_eligible is False
    assert isinstance(turns[0].outcome, Completed)
    response = turns[0].outcome.response
    assert response["routing_disposition"] == "clarify"
    assert response["response"]
    assert response["coverage"] == {
        "complete": False,
        "missing_requirement_ids": ["asset_id"],
    }
    assert response["task_plan"]["work_item_ids"] == []
    assert response["agent_outcomes"] == []
    assert runtime.get_stats() == {}
    replay = asyncio.run(adapter.run("dp-product-09-a"))[0]
    assert replay.outcome.response_id == turns[0].outcome.response_id
    assert replay.outcome.response == response
    assert messages == ["帮我看看这个是不是我需要的耳机麦克风？"]

    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_status"] == "NOT_RUN"
    assert manifest["promotion_allowed"] is False
