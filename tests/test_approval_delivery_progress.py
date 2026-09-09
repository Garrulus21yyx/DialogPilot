"""Delivery failures cannot erase or broaden a persisted operation scope."""
import asyncio
from types import SimpleNamespace

import pytest

from application.action_approval import approval_presentation_due
from application.response_assembly import ResponseAssembler
from tests.test_knowledge_answer_boundary import Verifier


@pytest.mark.parametrize("same", [True, False])
@pytest.mark.parametrize("published", [None, False, True])
def test_delivery_not_planner_mode_owns_representing(same, published):
    pending = SimpleNamespace(approval_id="a", version=2)
    previous = SimpleNamespace(approval_id="a", version=2 if same else 1)
    assert approval_presentation_due(pending, previous, published=published) == (
        not same or published is False)
    assert not approval_presentation_due(None, previous, published=published)


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
@pytest.mark.parametrize("failure", ["invalid_contract", "rejected", "author_failure"])
@pytest.mark.parametrize("wait", ["approval", "input"])
def test_expression_failure_retains_wait_without_authorizing_it(locale, failure, wait):
    class Author:
        async def compose(self, payload):
            if failure == "author_failure":
                raise TimeoutError("author unavailable")
            return "Candidate"

    class Review(Verifier):
        async def verify(self, *args, **kwargs):
            if failure == "invalid_contract":
                raise ValueError("structured_output_schema_invalid")
            return await super().verify(*args, **kwargs)

    context = ({"retained_approval": {"status": "AWAITING_DECISION_NOT_EXECUTED",
        "action_ref": "private_action", "arguments": {"private": "do-not-render"}}}
        if wait == "approval" else {"pending_interaction": True})
    result = asyncio.run(ResponseAssembler(Author(), knowledge_verifier=Review(False),
        fallback_locale=locale).assemble(None, current_message="What happened?",
            response_candidate="Candidate", conversation_context=context))
    assert not result.verified and not result.approval_operation_key
    assert "No result is available" not in result.text
    assert "do-not-render" not in result.text and "private_action" not in result.text
    if locale == "en":
        assert "saved" in result.text
        if wait == "approval":
            assert "has not executed" in result.text
    else:
        assert "保存" in result.text
    assert result.diagnostics


@pytest.mark.parametrize("wait", ["approval", "input"])
def test_knowledge_failure_preserves_wait_without_releasing_unsupported_evidence(wait):
    from tests.test_knowledge_answer_boundary import board
    context = ({"retained_approval": {"status": "AWAITING_DECISION_NOT_EXECUTED"}}
        if wait == "approval" else {"pending_interaction": True})
    class Review:
        async def verify(self, *args, **kwargs):
            raise ValueError("structured_output_schema_invalid")
    result = asyncio.run(ResponseAssembler(knowledge_verifier=Review(), fallback_locale="en").assemble(
        board("Unverified policy"), current_message="What happened?", response_candidate="Unverified policy",
        conversation_context=context))
    assert not result.verified and not result.approval_operation_key
    assert "saved" in result.text
    assert "Unverified policy" not in result.text
    assert result.diagnostics
