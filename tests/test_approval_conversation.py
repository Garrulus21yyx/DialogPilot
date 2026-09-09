from langgraph.store.memory import InMemoryStore
"""Prepared side effects and customer conversation have separate lifecycles."""
import asyncio
import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from application.default_capability_registry import build_default_capability_registry
from application.response_assembly import ResponseAssembler
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
    return TargetFrameworkAgent(model, tools, review_model=model, review_available_tokens=14200, result_store=InMemoryStore(), registry=registry, system_prompt="Assist."), _context(item), model, calls


def call(name, ident="proposal"):
    return AIMessage(content="", tool_calls=[{
        "name": name, "id": ident, "args": {"order_id": "DP1234"}}])


@pytest.mark.parametrize("extra", ["read", "duplicate", "failure"])
def test_preparation_retains_binding_and_ends_without_another_domain_call(extra):
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
    assert len(calls) == model.calls == 1
    assert result.candidate_response is None


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
                        "issues": [] if complete else ["Approval terms are incomplete."]},
                       name="submit_claim_checks")[ModelRole.INTENT]
        return await AnswerVerifier(model, model_profile=ModelProfile("test")).verify(*args, **kwargs)


@pytest.mark.parametrize("approval_status", ["ANSWERED", "LIMITATION", "MISSING"])
@pytest.mark.parametrize("approval_source", ["proposal", "persisted_flow"])
def test_approval_presentation_binds_original_scope_without_model_judge(approval_status, approval_source):
    agent, context, _, _ = domain([call("prepare_order_cancel"), AIMessage(content="Pending cancellation.")])
    result = asyncio.run(agent(context))
    text = "Order DP1234 is paid. Cancellation has not executed."
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
    assert len(verifier.calls) == 1  # Independent status answer, not scope completeness.
    evidence = json.loads(assembled.evidence_json)
    assert evidence["pending_actions"][0]["arguments"] == {"order_id": "DP1234", "expected_order_version": 4}
    assert evidence["pending_actions"][0]["effect_status"] == "NOT_EXECUTED"
    assert "DP1234" in assembled.text
    assert "paid" in assembled.text
    assert assembled.approval_operation_key == operation_key
    assert assembled.interaction_ready and not assembled.verified
    assert assembled.verification_reason == "PREPARED_SCOPE_RENDERED"


def test_pure_confirmation_question_does_not_exempt_embedded_facts():
    from services.claim_verification import make_request, assess
    req = make_request("Cancel?", "It costs $10. Approve?", {"price": 10})
    out = {"supported": False, "answered": True,
           "issues": ["Price has not been verified."]}
    result = assess(req, out)
    assert not hasattr(result, "approval_terms_complete")
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
@pytest.mark.parametrize("reply_only", [False, True])
@pytest.mark.parametrize("persisted", [False, True])
def test_application_publishes_verified_approval_and_can_recover_failed_description(first_verification, reply_only, persisted, request):
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
    from uuid import uuid4
    conversation_id = "approval-delivery-" + uuid4().hex

    pool = None
    if persisted:
        from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
        from infrastructure.postgres_target_runtime import PostgresConversationStateStore
        url = request.getfixturevalue("postgres_database_url")
        PostgresMigrationRunner(url).upgrade()
        pool = PostgresPool(PostgresPoolConfig(url, min_size=1, max_size=2))
        pool.open()
        request.addfinalizer(pool.close)

    async def run():
        agent, _, _, calls = domain([call("prepare_order_cancel"), AIMessage(content="Pending cancellation."),
                                     AIMessage(content="The cancellation is still pending.")])
        store = PostgresConversationStateStore(pool) if persisted else InMemoryConversationStateStore()
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
        text = "DP1234 is paid and has not been cancelled."
        composer = _Composer(lambda p: "\n".join([(text)]))
        verifier = ApprovalVerifier(first_verification)
        app = TargetChatApplication(manager=manager, admission=_Admission(), publication=publication,
            bundle_version=agent._registry.bundle_version,
            response_assembler=ResponseAssembler(composer, knowledge_verifier=verifier))
        command = ChatCommand(user_id="user-a", conv_id=conversation_id, request_id="request-1",
                              message="Cancel DP1234 and explain its status", tenant_id="tenant-a")
        outcome = await app.handle(command)
        assert isinstance(outcome, NeedsInput), outcome
        assert len(publication.questions) == 1
        question = publication.questions[0]
        assert "DP1234" in question["challenge"]
        assert len(verifier.calls) == 1
        assert question["resume_schema"]["properties"]["approval_id"]["const"] == outcome.signal_id
        assert question["expected_state_fingerprint"]
        assert len(calls) == 1
        assert "order.cancel:v1" not in question["challenge"]
        assert "expected_order_version" not in question["challenge"]
        if reply_only:
            async def conversational(*args, **kwargs):
                return TurnProposal(ProposalDisposition.RESPOND, (), "STATUS_REPLY",
                    response_text="The cancellation is still waiting.")
            manager._understanding = conversational
            if persisted:
                manager._state_store = PostgresConversationStateStore(pool)
            status = await app.handle(replace(command, request_id="request-3", message="Is it still waiting?"))
            assert isinstance(status, Completed)
            assert len(publication.questions) == 1
            assert len(calls) == 1
    asyncio.run(run())
