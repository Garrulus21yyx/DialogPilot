"""Owner-level invariants: no extra judge, no lost result, no preview as proof."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.store.memory import InMemoryStore

from application.agent_result import AgentResultStatus, MissingInputSpec
from application.response_assembly import ResponseAssembler
from infrastructure.target_model_context import delegated_task_content, delegated_working_input
from infrastructure.target_result_archive import TargetResultArchive, result_pointer
from infrastructure.domain_review_context import review_evidence_messages
from tests.test_target_framework_agent import _context, _manager, ScriptedToolModel
from tests.test_response_assembly import _board, _verified_order_result, _Composer


@pytest.mark.parametrize("question", ["Which color?", "你希望换哪种颜色？"])
def test_single_question_has_one_author_zero_review_and_no_factual_attestation(question):
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    async def run():
        class Forbidden:
            async def compose(self, *_args, **_kwargs):
                pytest.fail("single question must not be rewritten")
            async def verify(self, *_args, **_kwargs):
                pytest.fail("single question must not be model-judged")
        model = ScriptedToolModel(responses=[AIMessage("", tool_calls=[{
            "id": "ask", "name": "request_user_input", "args": {"question": question}}])])
        calls = []
        worker = TargetFrameworkAgent(model, _manager(calls), review_model=Forbidden(),
            review_available_tokens=1, result_store=InMemoryStore(),
            registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")
        result = await worker(_context())
        assert result.status is AgentResultStatus.NEEDS_USER_INPUT
        response = await ResponseAssembler(Forbidden(), knowledge_verifier=Forbidden()).assemble(
            _board(result), current_message="Help me choose", requested_inputs=result.missing_inputs)
        assert response.text == question and response.interaction_ready
        assert not response.verified and not response.approval_operation_key
        assert model.calls == 1 and calls == []
        assert result.missing_inputs[0].target_work_item_id == _context().work_item.work_item_id
    asyncio.run(run())


@pytest.mark.parametrize('unresolved', [False, True])
def test_retained_sibling_is_not_hidden_by_single_question(unresolved):
    from application.agent_result import AgentResult
    from tests.test_knowledge_answer_boundary import Verifier
    missing = (MissingInputSpec("reply", "ask", "INPUT", "string", "Which color?"),)
    waiting = AgentResult("ask", "product_technical", AgentResultStatus.NEEDS_USER_INPUT,
                          "INPUT", "test", missing_inputs=missing)
    from tests.test_target_framework_agent import _item
    prior = _verified_order_result()
    board = replace(_board(waiting), retained_outcomes=((replace(_item(), work_item_id=prior.work_item_id), None if unresolved else prior),))
    composer = _Composer("The order shipped. Which color?")
    response = asyncio.run(ResponseAssembler(composer, knowledge_verifier=Verifier(True)).assemble(
        board, current_message="Continue", requested_inputs=missing))
    assert response.composer_used and len(composer.calls) == 1


@pytest.mark.parametrize('inline_tokens', [256, 1024])
@pytest.mark.parametrize('size', [2000, 50000])
def test_large_result_view_is_small_but_archived_artifact_is_exact(inline_tokens, size):
    from infrastructure.target_context_compaction import ToolResultPersistence
    async def run():
        archive = TargetResultArchive(InMemoryStore())
        middleware = ToolResultPersistence(archive, max_inline_tokens=inline_tokens)
        content = json.dumps({'items': ['candidate'] * size, 'amount_minor': -1346})
        artifact = {'schema': 'tool-result-v1', 'result': {'success': True, 'data': json.loads(content)}}
        original = ToolMessage(content, tool_call_id='read', artifact=artifact)
        async def handler(_):
            return original
        output = await middleware.awrap_tool_call(SimpleNamespace(runtime=SimpleNamespace(context=_context())), handler)
        message, = output.update['messages']
        pointer = json.loads(message.content)
        assert pointer['complete'] is False and message.tool_call_id == original.tool_call_id
        assert count_tokens_approximately([message]) <= inline_tokens
        saved = await archive.load(_context(), pointer['result_ref'])
        assert saved == {'content': content, 'artifact': artifact}
        assert original.content == content
    asyncio.run(run())


@pytest.mark.parametrize("background_size", [1, 100, 10000])
def test_background_growth_does_not_grow_pinned_task_and_old_task_is_replaced(background_size):
    payload = {"objective": "Inspect only", "arguments": {"order_id": "A"},
        "requirements": [], "action_proposals_allowed": False,
        "source_conversation": {"current_message": "Do not submit."},
        "recent_relevant_turns": ["old context " * background_size],
        "verified_facts": [{"source_ref": "original", "value": "data " * background_size}],
        "pending_approval": None, "action_decisions": [], "completed_actions": []}
    content = delegated_task_content(payload)
    history = [HumanMessage("Old object B", id="task-context:old"),
               AIMessage("", tool_calls=[{"id": "read", "name": "lookup", "args": {}}]),
               ToolMessage("Original evidence", tool_call_id="read")]
    original = deepcopy(history)
    working, pinned = delegated_working_input(history, content, "new")
    assert count_tokens_approximately([pinned]) < 150
    assert len([m for m in working if (m.id or "").startswith("task-context:")]) == 1
    assert working[1:3] == history[1:]
    assert history == original
    assert "Do not submit." in str(pinned.content)
    assert "original" in str(working[0].content)


def test_unread_original_is_hydrated_for_action_review_but_explicit_pages_are_preserved():
    async def run():
        archive = TargetResultArchive(InMemoryStore())
        context = _context()
        original = json.dumps({"items": ["irrelevant"] * 5000, "final_constraint": "Cannot combine A and B"})
        ref = await archive.save(context, {"content": original})
        pointer = ToolMessage(result_pointer(ref, original), tool_call_id="read")
        assert "Cannot combine A and B" not in pointer.content
        view = await review_evidence_messages([pointer], context, archive)
        assert view[0].content == original and pointer.content != original
        page_call = AIMessage("", tool_calls=[{"id": "page", "name": "read_tool_result", "args": {"reference": ref}}])
        page = ToolMessage(json.dumps({"reference": ref, "text": "Cannot combine A and B"}), tool_call_id="page")
        messages = [pointer, page_call, page]
        assert await review_evidence_messages(messages, context, archive) == messages
        failed_page = page.model_copy(update={"status": "error"})
        assert (await review_evidence_messages([pointer, page_call, failed_page], context, archive))[0].content == original
        for text in (json.dumps({"reference": ref, "text": ""}), "[cleared]", pointer.content):
            unavailable = page.model_copy(update={"content": text})
            assert (await review_evidence_messages([pointer, page_call, unavailable], context, archive))[0].content == original
        nested = HumanMessage([{"type": "text", "text": json.dumps({"runtime_context": {
            "verified_facts": [{"source_ref": "read", "value": json.loads(pointer.content)}]}})}])
        restored, = await review_evidence_messages([nested], context, archive)
        assert json.loads(restored.content[0]['text'])['runtime_context']['verified_facts'][0]['value'] == json.loads(original)
        assert 'result_ref' in nested.content[0]['text']
    asyncio.run(run())


def test_oversized_action_evidence_requests_reading_without_invoking_judge(monkeypatch):
    from infrastructure.target_domain_outcome import DomainOutcomeReview
    async def forbidden(*_args, **_kwargs):
        pytest.fail("do not send oversized evidence or judge its preview")
    monkeypatch.setattr("infrastructure.target_domain_outcome.structured_call", forbidden)
    async def run():
        archive = TargetResultArchive(InMemoryStore())
        context = _context()
        original = "Full original evidence. " * 2000
        ref = await archive.save(context, {"content": original})
        review = DomainOutcomeReview(None, available_tokens=2500, business_policy="Policy", tools=[], archive=archive)
        verdict = await review.assess(context=context,
            messages=[ToolMessage(result_pointer(ref, original), tool_call_id="read")],
            kind="PREPARE_ACTION", candidate={"tool": "prepare", "arguments": {}})
        assert not verdict["accepted"] and "read_tool_result" in verdict["feedback"]
        assert verdict['model_called'] is False
    asyncio.run(run())
