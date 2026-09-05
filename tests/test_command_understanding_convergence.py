from __future__ import annotations

import json
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.default_flow_registry import command_primary_flow_registry
from application.active_case import (
    ActiveCaseContextView,
    ActiveCaseProjection,
    ActiveCaseSelection,
    ActiveCaseState,
)
from application.flow_retrieval import LexicalFlowRetriever
from application.turn_understanding import (
    ClarificationDecision,
    ClarificationReason,
    UnderstandingError,
    UnderstandingResult,
    UnderstandingStatus,
)
from evaluation.clarification_eval import score_clarification_dialogues
from evaluation.customer_service_command_gold import validate_command_gold
from evaluation.public_sgd.failure_attribution import FailureOwner, _first_failure
from core.model_policy import ModelProfile
from core.intent_recognizer import IntentCategory, IntentResult, UrgencyLevel
from infrastructure.command_primary_runtime import build_command_primary_chat_planner


def test_clarification_contract_is_closed_and_typed() -> None:
    with pytest.raises(UnderstandingError):
        UnderstandingResult(UnderstandingStatus.CLARIFY, reason_code="missing")
    with pytest.raises(UnderstandingError):
        ClarificationDecision(
            ClarificationReason.MULTIPLE_SUPPORTED_FLOWS,
            ("request_goal",),
            ("refund_status",),
        )


def test_failure_attribution_selects_only_earliest_owner() -> None:
    expected = {
        "status": "RESOLVED",
        "commands": [{
            "kind": "START_FLOW",
            "flow": {"flow_id": "a"},
            "arguments": {"id": "1"},
        }],
        "next_state": {},
    }
    actual = {
        "status": "RESOLVED",
        "commands": [{
            "kind": "START_FLOW",
            "flow": {"flow_id": "b"},
            "arguments": {"id": "2"},
        }],
        "next_state": {"changed": True},
    }
    assert _first_failure(expected, actual) is FailureOwner.FLOW_SELECTION


def test_flow_retrieval_is_deterministic_and_non_authoritative() -> None:
    registry = command_primary_flow_registry("tenant-1")
    retriever = LexicalFlowRetriever()
    first = retriever.retrieve("check refund eligibility for order", registry)
    second = retriever.retrieve("check refund eligibility for order", registry)
    assert first == second
    assert first[0].flow.flow_id == "refund_eligibility"


def test_clarification_dev_contract_scores_multi_turn_resolution() -> None:
    cases = [
        json.loads(line)
        for line in Path(
            "data/eval/customer-service-command-gold-v1/clarification-dev.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    predictions = [
        {
            "case_id": case["case_id"],
            "first_turn": {
                "status": case["expected_first_status"],
                "clarification": case["expected_clarification"],
            },
            "second_turn": case["expected_resolution"],
        }
        for case in cases
    ]
    score = score_clarification_dialogues(cases, predictions)
    assert score["one_clarification_resolution_rate"] == 1.0
    assert score["discriminating_question_rate"] == 1.0
    assert score["unnecessary_clarification_rate"] == 0.0
    assert score["oos_misclassification_rate"] == 0.0


def test_authoritative_structured_mode_claims_oos_and_failure() -> None:
    class Messages:
        def __init__(self, output: str) -> None:
            self.output = output

        async def create(self, **_kwargs):
            return SimpleNamespace(
                id="command-output",
                content=[SimpleNamespace(type="text", text=self.output)],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    async def prepare(output: str):
        planner = build_command_primary_chat_planner(
            {"COMMAND_PRIMARY_MODE": "structured_read_only_primary"},
            command_completion_client=SimpleNamespace(messages=Messages(output)),
            command_model_profile=ModelProfile("command-router-test"),
        )
        return await planner.prepare(
            message="给我播放一首歌",
            identity=SimpleNamespace(
                tenant_id="tenant-1",
                user_id="user-1",
                conversation_id="conversation-1",
                request_id="request-1",
            ),
            recent_messages=(),
            active_case_view=ActiveCaseContextView(
                ActiveCaseProjection(ActiveCaseState.NO_ACTIVE_CASE),
                ActiveCaseSelection((), ()),
            ),
            history=(),
            bundle=SimpleNamespace(version="bundle-v1"),
        )

    oos = asyncio.run(prepare(
        '{"status":"NO_SUPPORTED_FLOW","commands":[],"clarification":null}'
    ))
    invalid = asyncio.run(prepare("not-json"))
    assert oos.authority_claimed is True and oos.use_primary is True
    assert invalid.authority_claimed is True and invalid.use_primary is False
    tampered = replace(
        oos,
        intent_projection=IntentResult(
            intent=IntentCategory.ACCOUNT_SECURITY,
            confidence=1.0,
            urgency=UrgencyLevel.CRITICAL,
            intent_group="tampered",
            entities={"admin": True},
            reasoning="tampered",
            latency_ms=0.0,
            source_scores={"tampered": 1.0},
        ),
    )
    assert tampered.compatibility_projection == oos.compatibility_projection


def test_customer_service_command_gold_is_frozen_and_registry_bounded() -> None:
    report = validate_command_gold(
        Path("data/eval/customer-service-command-gold-v1"),
        command_primary_flow_registry("tenant-1"),
    )
    assert report == {
        "schema_version": "dialogpilot-customer-service-command-validation-v1",
        "status": "PASS",
        "counts": {"dev.jsonl": 8, "heldout.jsonl": 4},
        "heldout_consumed": False,
    }
