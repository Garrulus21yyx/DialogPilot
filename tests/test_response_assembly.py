import asyncio
import pytest

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    ReceiptRef,
)
from application.response_assembly import ResponseAssembler, ResponseAssemblyMode
from application.result_board import ResultBoardSnapshot
from tests.test_knowledge_answer_boundary import Verifier


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("repair", [False, True])
def test_policy_snapshot_reaches_author_and_verifier_without_extra_calls(direct, repair):
    import json
    from dataclasses import replace
    from application.default_capability_registry import build_default_capability_registry
    registry = build_default_capability_registry("tenant-a")
    registry = replace(registry, agents=tuple(replace(agent,
        business_policy=f"Policy for {agent.agent_id}: action A prevents subsequent action B.")
        for agent in registry.agents))
    author_inputs = []
    class Author:
        async def compose(self, payload):
            author_inputs.append(payload)
            return "The operations must be assessed together."
    class Review(Verifier):
        async def verify(self, *args, **kwargs):
            self.passed = not repair or bool(self.calls)
            return await super().verify(*args, **kwargs)
    verifier = Review(True)
    from infrastructure.conversation_tool_catalog import ConversationToolCatalog
    from tests.test_approval_conversation import domain
    worker, _, _, _ = domain([])
    semantics = ConversationToolCatalog(worker._tool_manager).action_semantics(worker._registry)
    result = asyncio.run(ResponseAssembler(Author(), registry=registry, action_semantics=semantics,
        knowledge_verifier=verifier).assemble(
        None if direct else _board(_verified_order_result()), current_message="Can I do both?",
        response_candidate="Both operations are possible." if direct else None,
        conversation_context={"historical_assistant": "Everything is permitted.",
            "turn_execution": {"phase": "REPLY", "continues_after_reply": False,
                "waiting_for_input": False, "waiting_for_approval": False}}))
    assert result.verified
    evidence = json.loads(result.evidence_json)
    policy = evidence["capability_policy"]
    assert evidence["turn_execution"] == {"phase": "REPLY", "continues_after_reply": False,
        "waiting_for_input": False, "waiting_for_approval": False}
    assert policy["registry_fingerprint"] == registry.fingerprint
    assert policy["bundle_version"] == registry.bundle_version
    assert policy["business_actions"] == list(semantics)
    assert policy["agents"] == [{"agent_id": agent.agent_id, "description": agent.description,
                                 "business_policy": agent.business_policy}
                                for agent in registry.agents]
    assert all(json.loads(kwargs["context"]) == evidence for _, kwargs in verifier.calls)
    assert all(payload["evidence"] == evidence for payload in author_inputs)
    assert len(verifier.calls) == 1 + int(repair)
    assert len(author_inputs) == int(not direct) + int(repair)


def test_policy_revision_changes_response_evidence_identity():
    from dataclasses import replace
    from application.default_capability_registry import build_default_capability_registry
    registry = build_default_capability_registry("tenant-a")
    revised = replace(registry, agents=(replace(registry.agents[0], business_policy="Changed applicability."),
                                       *registry.agents[1:]))
    responses = [asyncio.run(ResponseAssembler(registry=value, knowledge_verifier=Verifier(True)).assemble(
        None, current_message="Explain policy", response_candidate="Policy explanation."))
        for value in (registry, revised)]
    assert responses[0].evidence_sha256 != responses[1].evidence_sha256


def _result(
    work_item_id, owner, status=AgentResultStatus.SUCCEEDED,
    response=None, reason="DONE", receipts=(),
):
    return AgentResult(
        work_item_id, owner, status, reason, "worker-v1",
        action_receipts=receipts, candidate_response=response,
        retryable=status is AgentResultStatus.RETRYABLE_FAILURE,
    )


def _board(*results, missing=(), conflicts=(), partial=False):
    return ResultBoardSnapshot(
        results, (), (), (), missing, conflicts, True, partial,
    )


@pytest.mark.parametrize("same_target", [False, True])
@pytest.mark.parametrize("repeated_local_id", [False, True])
@pytest.mark.parametrize("copied_receipt", [False, True])
def test_committed_and_pending_operations_keep_their_own_contracts(same_target, repeated_local_id, copied_receipt):
    from dataclasses import replace
    from application.response_assembly import _response_context, _allowed_claims
    from application.work_item import ArgumentValue
    from tests.test_write_workflow import _item as write_item
    from tests.test_work_control import _item as read_item
    committed = write_item("executed-operation")
    pending = replace(write_item("proposed-operation"),
        aggregate_ref=committed.aggregate_ref if same_target else "order:DP9876",
        arguments=(ArgumentValue.create("order_id", "DP1234" if same_target else "DP9876"),))
    receipt = ReceiptRef("committed-receipt", "v1", committed.operation_key,
                         "COMMITTED", "refund.request_action")
    completed = _result(committed.work_item_id, committed.owner_agent, receipts=(receipt,))
    current = replace(read_item("current", 1, work_item_id=committed.work_item_id if repeated_local_id else "current"),
                      owner_agent=committed.owner_agent)
    waiting = replace(_result(current.work_item_id, current.owner_agent,
        status=AgentResultStatus.WAITING_APPROVAL, receipts=(receipt,) if copied_receipt else ()), pending_action=pending)
    board = replace(_board(waiting), work_items=(current,), retained_outcomes=((committed, completed),))
    context = _response_context(board)
    assert context["receipts"][0]["operation_key"] == committed.operation_key
    assert context["receipts"][0]["action"]["target_entity_ref"] == committed.aggregate_ref
    assert context["receipts"][0]["action"]["arguments"] == {arg.name: arg.value for arg in committed.arguments}
    assert context["pending_actions"][0]["operation_key"] == pending.operation_key
    assert context["pending_actions"][0]["target_entity_ref"] == pending.aggregate_ref
    assert context["receipts"][0]["effect_status"] == "COMMITTED"
    assert context["pending_actions"][0]["effect_status"] == "NOT_EXECUTED"
    assert all(claim.value["operation_key"] == receipt.operation_key
               for claim in _allowed_claims(board) if claim.kind == "RECEIPT")
    if copied_receipt:
        # A continuation may carry a receipt, but its own work contract is not
        # the executed action. Never join it to the new proposal by local ID.
        assert context["receipts"][1]["action"] is None
    assert board.retained_outcomes[0] == (committed, completed)


def _verified_order_result(work_item_id='o', order_id='DP1234', response=None):
    import json
    from dataclasses import replace
    from datetime import datetime, timezone
    from application.agent_result import FactRecord, FactSourceKind
    fact=FactRecord('order:'+order_id,'order.current_state',
        json.dumps({'order_id':order_id,'status':'shipped'},sort_keys=True,separators=(',',':')),
        FactSourceKind.VERIFIED_STATE,'read:1','order_lookup','order-view-v2',datetime.now(timezone.utc))
    return replace(_result(work_item_id,'order_logistics',response=response),facts=(fact,))


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
@pytest.mark.parametrize("order_id", ["DP1234", "DP9876"])
def test_pre_write_lookup_is_an_observation_not_current_state_in_fallback(locale, order_id):
    from application.response_assembly import _render_board
    earlier = _verified_order_result(order_id=order_id)
    committed = _result("change", "order_logistics", receipts=(ReceiptRef(
        "receipt-change", "v1", "operation-change", "COMMITTED", "order.cancel_action",
    ),))
    text = _render_board(_board(earlier, committed), locale=locale)
    assert earlier.facts[0].observed_at.isoformat() in text
    assert order_id in text
    assert "current recorded status" not in text and "当前状态" not in text
    assert ("submitted" if locale == "en" else "已提交") in text


def test_safe_rendering_preserves_verified_success_without_publishing_worker_drafts():
    from application.response_assembly import _render_board
    board = _board(_verified_order_result(response="UNVERIFIED promise"),
                   _result("blocked", "product_technical", AgentResultStatus.BLOCKED,
                           response="UNVERIFIED failure explanation"))
    text = _render_board(board)
    assert "DP1234" in text
    assert "UNVERIFIED" not in text
    assert len(board.results) == 2


class _Composer:
    def __init__(self, response):
        self.response, self.calls = response, []

    async def compose(self, payload):
        self.calls.append(payload)
        return self.response(payload) if callable(self.response) else self.response


@pytest.mark.parametrize("invalid_input", [False, True])
def test_verifier_feedback_only_revises_reply_from_same_evidence(invalid_input):
    from application.agent_result import MissingInputSpec
    from services.answer_verifier import AnswerVerifier
    from core.model_policy import ModelProfile, ModelRole
    from tests.framework_structured_stub import models
    output = {"supported": True, "answered": False, "approval_terms_complete": False,
        "issues": ["There is no missing choice" if invalid_input else "Ask only for the missing choice"]}
    model = models(output, name="submit_claim_checks")[ModelRole.INTENT]
    composer = _Composer("May I proceed?")
    missing = (MissingInputSpec("reply", "work", "INPUT", "string", "May I proceed?"),)
    result = asyncio.run(ResponseAssembler(composer,
        knowledge_verifier=AnswerVerifier(model, model_profile=ModelProfile("test"))).assemble(
            _board(AgentResult("work", "order_logistics", AgentResultStatus.NEEDS_USER_INPUT,
                "INPUT", "test", missing_inputs=missing), _verified_order_result()),
            current_message="Please process my request", requested_inputs=missing))
    assert not result.verified
    assert len(composer.calls) == 2
    assert not hasattr(result, "rejected_input_work_items")
    assert composer.calls[0]["evidence"] == composer.calls[1]["evidence"]
    assert result.evidence_json


@pytest.mark.parametrize("owner", ["general", "order_logistics", "billing_refund", "retail"])
def test_working_notes_are_not_facts_or_a_second_public_author(owner):
    composer = _Composer("已收到你的请求。")
    result = asyncio.run(ResponseAssembler(composer, knowledge_verifier=Verifier(True)).assemble(
        _board(_result("w", owner, response="INTERNAL: I should claim it shipped.")), current_message="查询"))
    assert result.verified and result.text == "已收到你的请求。"
    payload = composer.calls[0]
    assert "INTERNAL" in payload["domain_notes"][0]["text"]
    assert "INTERNAL" not in str(payload["evidence"])
    assert not {"allowed_claims", "support_catalog", "statement_catalog"} & payload.keys()


@pytest.mark.parametrize("value", [None, {}, {"segments": [{"text": "hello"}]}, "", 12])
def test_old_protocol_and_empty_values_are_not_a_second_execution_path(value):
    result = asyncio.run(ResponseAssembler(_Composer(value), knowledge_verifier=Verifier(True)).assemble(
        _board(_verified_order_result()), current_message="查询"))
    assert not result.verified
    assert result.verification_reason == "composition_model:ValueError"


@pytest.mark.parametrize("paragraphs", [1, 2, 5, 25])
@pytest.mark.parametrize("supported", [False, True])
def test_layout_is_not_evidence_and_whole_answer_is_verified(paragraphs, supported):
    text = "\\n".join(["查询结果，请问还有什么需要？"] * paragraphs)
    verifier = Verifier(supported)
    response = asyncio.run(ResponseAssembler(_Composer(text), knowledge_verifier=verifier).assemble(
        _board(_verified_order_result()), current_message="查询"))
    assert response.verified is supported
    assert all(call[0][1] == text for call in verifier.calls)


def test_no_verifier_does_not_publish_worker_or_composer_claims():
    response = asyncio.run(ResponseAssembler(_Composer("保证退款")).assemble(
        _board(_verified_order_result(response="保证退款")), current_message="查询"))
    assert not response.verified and "保证退款" not in response.text
    assert "已发货" in response.text


def test_native_response_keeps_evidence_snapshot_and_hash():
    import json, hashlib
    from dataclasses import replace
    composer, verifier = _Composer("已发货。"), Verifier(True)
    response = asyncio.run(ResponseAssembler(composer, knowledge_verifier=verifier).assemble(
        _board(_verified_order_result()), current_message="查询"))
    captured = json.loads(response.evidence_json)
    assert captured == composer.calls[0]["evidence"]
    assert captured == json.loads(verifier.calls[0][1]["context"])
    assert response.evidence_sha256 == hashlib.sha256(response.evidence_json.encode()).hexdigest()
    assert response.evidence_refs == ("read:1",)
    with pytest.raises(ValueError):
        replace(response, evidence_json="{}")
    with pytest.raises(ValueError):
        replace(response, text="已经退款")


@pytest.mark.parametrize("status", [AgentResultStatus.PARTIAL, AgentResultStatus.BLOCKED,
    AgentResultStatus.NEEDS_USER_INPUT, AgentResultStatus.TERMINAL_FAILURE])
def test_all_outcomes_reach_the_semantic_check_without_selection_markers(status):
    import json
    verifier = Verifier(False)
    from application.agent_result import MissingInputSpec
    waiting = (AgentResult("second", "retail", status, "MISSING", "test",
        missing_inputs=(MissingInputSpec("reply", "second", "MISSING", "string", "Which option?"),))
        if status is AgentResultStatus.NEEDS_USER_INPUT else _result("second", "retail", status))
    board = _board(_verified_order_result(), waiting)
    response = asyncio.run(ResponseAssembler(_Composer("只回答订单"), knowledge_verifier=verifier).assemble(
        board, current_message="查订单和另一个任务"))
    context = json.loads(verifier.calls[0][1]["context"])
    assert len(context["outcomes"]) == 2
    assert context["outcomes"][1]["status"] == status.value
    assert "unrepresented_outcomes" not in context
    assert not response.verified


def test_typed_failure_does_not_discard_committed_receipt():
    from core.framework_models import ModelInvocationError
    class Broken:
        async def compose(self, payload):
            raise ModelInvocationError("compose_response", TimeoutError())
    receipt = ReceiptRef("receipt-1", "v1", "op-1", "COMMITTED", "refund.action")
    board = _board(_result("w", "retail", response="working", receipts=(receipt,)))
    response = asyncio.run(ResponseAssembler(Broken()).assemble(board, current_message="处理了吗"))
    assert not response.verified and response.retryable
    assert "请求已提交" in response.text
    assert response.diagnostics[0].stage == "composition_model"
    assert board.results[0].action_receipts == (receipt,)
