from langgraph.store.memory import InMemoryStore
"""Prepared side effects and customer conversation have separate lifecycles."""
import asyncio
import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from application.default_capability_registry import build_default_capability_registry
from application.response_assembly import ResponseAssembler, _APPROVAL_DESCRIPTION_REQUIREMENT
from infrastructure.target_framework_agent import TargetFrameworkAgent
from mcp.tool_manager import MCPToolManager, Tool
from tests.test_target_framework_agent import ScriptedToolModel, _item, _context
from tests.test_response_assembly import _board, _Composer
from tests.test_knowledge_answer_boundary import Verifier


def domain(responses):
    base = build_default_capability_registry("tenant-a")
    action = replace(base.action("order.cancel:v1"), flow_ref=None)
    owner = replace(base.agent("order_logistics"), allowed_skill_ids=(),
                    allowed_tool_ids=("order_lookup", "order_cancel", "order_cancel_status"))
    registry = replace(base, agents=(owner,), actions=(action,), flows=(), skills=())
    calls = []
    tools = MCPToolManager("test-key", model="test-model")

    async def read(params, context):
        calls.append(("read", params))
        return {"order_id": params["order_id"], "status": "paid", "version": 4}

    async def write(params, context):
        pytest.fail("proposal loop cannot execute a write")

    schema = {"type": "object", "properties": {"order_id": {"type": "string"}},
              "required": ["order_id"], "additionalProperties": False}
    tools.register(Tool("order_lookup", "Read order", read, schema,
                        allowed_agents=("general",), authority="order.current_state"))
    tools.register(Tool("order_cancel_status", "Read cancellation", read, schema,
                        allowed_agents=("general",), authority="order.cancellation_state"))
    tools.register(Tool("order_cancel", "Cancel order", write, schema,
                        allowed_agents=("general",), authority="order.cancel_action", read_only=False))
    item = replace(_item(), owner_agent=owner.agent_id, objective="Cancel DP1234 and explain its current status",
                   allowed_tools=("order_lookup",), allowed_actions=(action.ref,), arguments=(),
                   requirement_ids=(), registry_fingerprint=registry.fingerprint, max_steps=8)
    model = ScriptedToolModel(responses=responses)
    return TargetFrameworkAgent(model, tools, result_store=InMemoryStore(), registry=registry, system_prompt="Assist."), _context(item), model, calls


def call(name, ident="proposal"):
    return AIMessage(content="", tool_calls=[{
        "name": name, "id": ident, "args": {"order_id": "DP1234"}}])


@pytest.mark.parametrize("extra", ["read", "duplicate", "failure"])
def test_preparation_retains_binding_and_allows_read_and_reply(extra):
    responses = [call("prepare_order_cancel")]
    if extra == "read":
        responses.append(call("order_lookup", "read-after-proposal"))
    if extra == "duplicate":
        responses.append(call("prepare_order_cancel", "duplicate-proposal"))
    if extra != "failure":
        responses.append(AIMessage(content="Order DP1234 is paid and has not been cancelled. Shall I cancel it?"))
    agent, context, model, calls = domain(responses)
    result = asyncio.run(agent(context))
    assert result.status.value == "WAITING_APPROVAL"
    assert result.pending_action is not None
    assert result.pending_action.work_item_id.endswith(":action:proposal")
    assert result.action_receipts == ()
    assert len(calls) == (2 if extra == "read" else 1)
    if extra != "failure":
        assert result.candidate_response == responses[-1].text
        assert model.calls == len(responses)
    else:
        assert result.candidate_response is None
        assert "execution_feedback" in json.dumps(result.working_messages)


class ApprovalVerifier(Verifier):
    def __init__(self, approval_status="ANSWERED"):
        super().__init__(True)
        self.approval_status = approval_status

    async def verify(self, *args, **kwargs):
        from services.answer_verifier import AnswerVerifier
        from core.model_policy import ModelProfile, ModelRole
        from tests.framework_structured_stub import models
        self.calls.append((args, kwargs))
        complete = self.approval_status == "ANSWERED"
        model = models({"supported": True, "answered": True,
                        "approval_terms_complete": complete,
                        "rejected_input_work_items": [],
                        "issues": [] if complete else ["Approval terms are incomplete."]},
                       name="submit_claim_checks")[ModelRole.INTENT]
        return await AnswerVerifier(model, model_profile=ModelProfile("test")).verify(*args, **kwargs)


@pytest.mark.parametrize("approval_status", ["ANSWERED", "LIMITATION", "MISSING"])
@pytest.mark.parametrize("approval_source", ["proposal", "persisted_flow"])
def test_approval_presentation_requires_original_action_and_verified_answer(approval_status, approval_source):
    agent, context, _, _ = domain([call("prepare_order_cancel"), AIMessage(content="Pending cancellation.")])
    result = asyncio.run(agent(context))
    text = "Order DP1234 is paid. Cancellation has not executed. Shall I cancel that order?"
    composer = _Composer(lambda p: "\n".join([(text)]))
    verifier = ApprovalVerifier(approval_status)
    pending = None
    operation_key = result.pending_action.operation_key
    if approval_source == "persisted_flow":
        from application.conversation_state import PendingApprovalState
        action = result.pending_action
        pending = PendingApprovalState(action.approval_binding, 1, "flow", action.work_item_id,
            action.action_ref, action.operation_key, action.aggregate_ref, action.target_entity_version,
            "2099-01-01T00:00:00+00:00", arguments=action.arguments)
        result = replace(result, pending_action=None)
    assembled = asyncio.run(ResponseAssembler(composer, knowledge_verifier=verifier).assemble(
        _board(result), current_message="Cancel it and explain its current status", pending_approval=pending))
    evidence = json.loads(verifier.calls[0][1]["context"])
    assert evidence["pending_actions"][0]["arguments"] == {"order_id": "DP1234", "expected_order_version": 4}
    assert evidence["pending_actions"][0]["effect_status"] == "NOT_EXECUTED"
    if approval_status == "ANSWERED":
        assert assembled.text == text
        assert assembled.approval_operation_key == operation_key
        assert assembled.verified_text_sha256
    else:
        assert not assembled.approval_operation_key
        assert not assembled.verified_text_sha256


def test_pure_confirmation_question_does_not_exempt_embedded_facts():
    from services.claim_verification import make_request, assess
    req = make_request("Cancel?", "It costs $10. Approve?", {"price": 10})
    out = {"supported": False, "answered": True, "approval_terms_complete": True,
           "rejected_input_work_items": [],
           "issues": ["Price has not been verified."]}
    result = assess(req, out)
    assert result.approval_terms_complete
    assert not result.supported


def test_approval_presentation_cannot_be_attached_to_unverified_or_changed_text():
    import hashlib
    from application.response_assembly import AssembledResponse, ResponseAssemblyMode
    response = AssembledResponse("Approve?", ResponseAssemblyMode.CONVERSATION_COMPOSE,
        (), True, "PASS", "ANSWER_SUPPORT_CHECKED", hashlib.sha256(b"Approve?").hexdigest(), "operation")
    for update in ({"text": "Already executed"}, {"verified_text_sha256": ""}, {"verification_status": "UNKNOWN"}):
        with pytest.raises(ValueError):
            replace(response, **update)


@pytest.mark.parametrize("first_verification", ["ANSWERED", "LIMITATION"])
def test_application_publishes_verified_approval_and_can_recover_failed_description(first_verification):
    from application.chat_contracts import ChatCommand, Completed, NeedsInput
    from application.conversation_state import InMemoryConversationStateStore
    from application.orchestration_runtime import OrchestrationRuntime
    from application.target_conversation_manager import TargetConversationManager
    from application.target_chat_application import TargetChatApplication
    from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    from langgraph.checkpoint.memory import InMemorySaver
    from tests.test_target_persistence_and_manager import _ResumeAwareUnderstanding
    from tests.test_target_chat_cutover import _Admission, _Publication

    async def run():
        agent, _, _, calls = domain([call("prepare_order_cancel"), AIMessage(content="Pending cancellation."),
                                     AIMessage(content="The cancellation is still pending.")])
        store = InMemoryConversationStateStore()
        manager = TargetConversationManager(state_store=store, registry=agent._registry,
            understanding=_ResumeAwareUnderstanding(TurnProposal(ProposalDisposition.RESOLVED,
                (CommandProposal("open-order", CommandKind.DELEGATE_TASK, "order_logistics",
                                 "Cancel DP1234 and explain its status", allow_action_proposals=True),), "OPEN")),
            orchestration=OrchestrationRuntime(direct_executor=agent, domain_workers={"order_logistics": agent},
                checkpointer=InMemorySaver(serde=target_checkpoint_serializer())))
        class Publication(_Publication):
            questions = []
            def publish_interaction(self, *args, **kwargs):
                self.questions.append(kwargs)
                return super().publish_interaction(*args, **kwargs)
        publication = Publication()
        text = "DP1234 is paid and has not been cancelled. Shall I cancel that order?"
        composer = _Composer(lambda p: "\n".join([(text)]))
        verifier = ApprovalVerifier(first_verification)
        app = TargetChatApplication(manager=manager, admission=_Admission(), publication=publication,
            bundle_version=agent._registry.bundle_version,
            response_assembler=ResponseAssembler(composer, knowledge_verifier=verifier))
        command = ChatCommand(user_id="user-a", conv_id="conversation-a", request_id="request-1",
                              message="Cancel DP1234 and explain its status", tenant_id="tenant-a")
        outcome = await app.handle(command)
        if first_verification != "ANSWERED":
            assert isinstance(outcome, Completed), outcome
            assert publication.questions == []
            verifier.approval_status = "ANSWERED"
            outcome = await app.handle(replace(command, request_id="request-2", message="Explain the pending cancellation again"))
        assert isinstance(outcome, NeedsInput), outcome
        assert len(publication.questions) == 1
        question = publication.questions[0]
        assert question["challenge"] == text
        assert question["resume_schema"]["properties"]["approval_id"]["const"] == outcome.signal_id
        assert question["expected_work_controls"]
        assert len(calls) == 1
        assert "order.cancel:v1" not in question["challenge"]
        assert "expected_order_version" not in question["challenge"]
    asyncio.run(run())
