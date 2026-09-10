"""Cross-boundary invariants, independent of any retail item or wording."""
import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from application.response_assembly import ResponseAssembler, _response_context
from application.response_evidence import composition_evidence
from tests.test_approval_operation_set import prepared
from tests.test_knowledge_answer_boundary import Verifier, board as knowledge_board
from tests.test_response_assembly import _Composer, _board


@pytest.mark.parametrize("count", [1, 2, 4])
@pytest.mark.parametrize("failure", ["author", "review", "missing_author"])
def test_failed_presentation_retains_all_operations_without_publishing_a_grant(count, failure):
    _, _, result, state = prepared(count)
    before = deepcopy(state)
    class Author:
        async def compose(self, payload):
            if failure == "author":
                raise TimeoutError("model unavailable")
            return "Unverified explanation. Approve?"
    response = asyncio.run(ResponseAssembler(
        None if failure == "missing_author" else Author(), knowledge_verifier=Verifier(False)
    ).assemble(_board(result), current_message="continue", pending_approval=state.pending_approval))
    assert not response.interaction_ready and not response.approval_operation_key
    assert "Approve?" not in response.text
    assert state == before
    assert len(state.pending_approval.operations) == count


@pytest.mark.parametrize("count", [1, 2, 4])
def test_revision_keeps_the_full_scope_and_only_retries_expression(count):
    _, _, result, state = prepared(count)
    composer = _Composer(lambda p: "Corrected full confirmation?" if "repair_feedback" in p else "Incomplete")
    class Review(Verifier):
        async def verify(self, *args, **kwargs):
            self.passed = bool(self.calls)
            return await super().verify(*args, **kwargs)
    review = Review(False)
    response = asyncio.run(ResponseAssembler(composer, knowledge_verifier=review).assemble(
        _board(result), current_message="continue", pending_approval=state.pending_approval,
        conversation_context={"turn_execution": {"continues_after_reply": False}}))
    assert response.interaction_ready
    assert len(composer.calls) == len(review.calls) == 2
    snapshots = [p["evidence"] for p in composer.calls]
    assert snapshots[0] == snapshots[1] == json.loads(response.evidence_json)
    assert all(json.loads(kw["context"]) == snapshots[0] for _, kw in review.calls)
    assert len(snapshots[0]["pending_actions"]) == count
    assert response.text == "Corrected full confirmation?"
    assert response.approval_operation_key == state.pending_approval.scope_key


def test_display_projection_preserves_business_values_not_runtime_citations():
    business = {"order_id": "DP1000", "source_ref": "customer-visible-reference",
                "items": [{"item_id": "new", "name": "Bottle", "price": 54.85}]}
    snapshot = {
        "facts": [{"source_ref": "call-private", "producer_id": "worker",
                   "observed_at": "2026-09-10", "value": business}],
        "pending_actions": [{"operation_key": "private-op", "action_ref": "modify:v1",
                             "arguments": {"order_id": "DP1000"}, "effect_status": "NOT_EXECUTED"}],
        "receipts": [{"receipt_id": "private-receipt", "operation_key": "done",
                      "effect_status": "COMMITTED", "action": {"arguments": {"order_id": "DP2000"}}}],
        "outcomes": [{"receipt_ids": ["private-receipt"]}],
    }
    original = deepcopy(snapshot)
    display = composition_evidence(snapshot)
    assert snapshot == original
    assert display["facts"][0]["value"] == business
    assert display["facts"][0]["observed_at"] == "2026-09-10"
    assert display["pending_actions"][0]["effect_status"] == "NOT_EXECUTED"
    assert display["receipts"][0]["effect_status"] == "COMMITTED"
    assert display["outcomes"][0]["receipt_indexes"] == [0]
    assert display["public_citations"] == []
    for identifier in ("call-private", "private-op", "private-receipt"):
        assert identifier not in json.dumps(display)
        assert identifier in json.dumps(original)


def test_knowledge_citations_come_only_from_knowledge_evidence():
    from application.knowledge_tool_contract import evidence_id
    snapshot = _response_context(knowledge_board(""))
    display = composition_evidence(snapshot)
    assert [row["evidence_id"] for row in display["public_citations"]] == [evidence_id("child-1")]
    assert display["facts"][0]["value"] == snapshot["facts"][0]["value"]


def test_removed_card_checkpoint_requires_regeneration_not_silent_null():
    from dataclasses import make_dataclass
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer, TargetCheckpointContractError
    old = make_dataclass("AssembledResponse", [("verification_reason", str)],
                         namespace={"__module__": "application.response_assembly"})
    old.__module__ = "application.response_assembly"
    codec = target_checkpoint_serializer()
    stored = codec.dumps_typed({"assembled": old("PREPARED_SCOPE_RENDERED")})
    with pytest.raises(TargetCheckpointContractError, match="reply regeneration"):
        codec.loads_typed(stored)


@pytest.mark.parametrize("bounded", [False, True])
def test_real_historical_envelope_keeps_contents_and_incomplete_preview(bounded):
    from infrastructure.postgres_conversation_evidence import PostgresConversationEvidence
    from application.business_observation import capture_business_observations
    from tests.test_business_observation_continuity import observed_board
    observations = capture_business_observations(observed_board())
    entries = PostgresConversationEvidence._business([("private-publication", {"business_observations": observations})])
    assert entries
    fact = entries[0]["observation"]["facts"][0]
    fact["source_ref"] = "private-fact-source"
    if bounded:
        fact.pop("value_json")
        fact["value_reference"] = {"status": "NOT_EXPANDED", "preview": "order fragment",
            "complete": False, "total_characters": 9000, "tool": "read_conversation_observation",
            "arguments": {"publication_id": "private-publication", "observation_id": "private-observation"}}
    snapshot = {"user_context": {"business_observations": entries,
        "summary": {"content": "Customer chose blue.", "source_ref": "private-summary"},
        "recent_messages": [{"role": "user", "content": "Use blue", "source_ref": "private-message"}],
        "evidence_refs": ["private-ref"], "observed_execution": {"facts": [deepcopy(fact)]}}}
    original = deepcopy(snapshot)
    display = composition_evidence(snapshot)
    text = json.dumps(display)
    for ref in ("private-publication", "private-fact-source", "private-summary", "private-message", "private-ref"):
        assert ref not in text
    projected = display["user_context"]["business_observations"][0]
    assert projected["status"] == "HISTORICAL"
    assert snapshot == original
    shown = projected["observation"]["facts"][0]
    if bounded:
        assert shown["value_reference"]["complete"] is False
        assert shown["value_reference"]["preview"] == "order fragment"
    else:
        assert shown["value_json"] == fact["value_json"]


@pytest.mark.parametrize("retained", [False, True])
def test_absent_selected_scope_never_instructs_fresh_confirmation(retained):
    from application.action_approval import reply_presentation_instruction
    instruction = reply_presentation_instruction({"pending_actions": [],
        "user_context": {"retained_approval": {"operations": [{"operation_key": "old"}]}} if retained else {}})
    assert "NO prepared action" in instruction
    assert "Ask once for approval" not in instruction


@pytest.mark.parametrize("mixed", [False, True])
def test_native_provider_receives_display_scope_without_contradictory_input_instruction(mixed):
    from core.model_policy import ModelProfile, ModelRole
    from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
    from tests.framework_structured_stub import StructuredStub
    captured = []
    class Capture(StructuredStub):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            captured.extend(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
    model = Capture(responses=[AIMessage(content="Which option? Shall I apply the prepared change?")])
    provider = AnthropicConversationPlanningProvider({ModelRole.SYNTHESIS: model},
        model_profile=ModelProfile("test"), synthesis_profile=ModelProfile("test"))
    snapshot = {"pending_actions": [{"operation_key": "private-op", "arguments": {"order_id": "DP1000"}}],
                "requested_inputs": [{"question_hint": "Which option?"}] if mixed else [],
                "turn_execution": {"continues_after_reply": False}}
    asyncio.run(provider.compose({"current_message": "continue", "evidence": snapshot}))
    system, message = captured
    assert "NOT permission to execute" not in system.content
    assert "normal continuation point" in system.content
    assert "confirmation card will be appended" in system.content
    content = json.loads(message.content)["evidence"]
    assert content["pending_actions"][0]["arguments"] == {"order_id": "DP1000"}
    assert "private-op" not in message.content
    assert snapshot["pending_actions"][0]["operation_key"] == "private-op"
