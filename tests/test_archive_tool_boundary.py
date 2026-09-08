"""Optional SDK reads can correct references; required restoration stays strict."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage, messages_to_dict
from langgraph.store.memory import InMemoryStore

from application.default_capability_registry import build_default_capability_registry
from application.work_item import WorkControlBinding
from infrastructure.target_framework_agent import TargetFrameworkAgent
from infrastructure.target_result_archive import (
    TargetResultArchive, ResultArchiveError, ResultReferenceNotFound,
)
from tests.test_target_framework_agent import ScriptedToolModel, _context, _manager


def invocation(name, args, ident):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident}])


@pytest.mark.parametrize("kind", ["missing", "foreign", "evidence"])
@pytest.mark.parametrize("batched", [False, True])
def test_optional_missing_reference_returns_sdk_error_then_allows_real_progress(kind, batched):
    async def run():
        store, context, calls = InMemoryStore(), _context(), []
        archive = TargetResultArchive(store)
        owner = replace(context, trusted_context={**context.trusted_context, "user_id": "other"}) if kind == "foreign" else context
        reference = await archive.save(owner, {"content": "private original"})
        args = {"reference": "nonexistent" if kind == "missing" else reference}
        if kind == "evidence":
            args["evidence_id"] = "not-in-directory"
        first = invocation("read_tool_result", args, "bad-reference")
        second = invocation("catalog_search", {"query": "current product"}, "catalog")
        responses = ([AIMessage(content="", tool_calls=first.tool_calls + second.tool_calls)]
                     if batched else [first, second])
        model = ScriptedToolModel(responses=[*responses, AIMessage(content="The catalog identifies PX-200.")])
        worker = TargetFrameworkAgent(model, _manager(calls), review_model=model,
            review_available_tokens=14200, result_store=store,
            registry=build_default_capability_registry("tenant-a"), system_prompt="Identify the product.")
        result = await worker(context)
        assert result.status.value == "SUCCEEDED"
        assert model.calls == (2 if batched else 3) and len(calls) == 1
        errors = [entry["data"] for entry in result.working_messages
                  if entry["type"] == "tool" and entry["data"]["tool_call_id"] == "bad-reference"]
        assert len(errors) == 1 and errors[0]["status"] == "error"
        assert "ARCHIVE_REFERENCE_NOT_FOUND" in errors[0]["content"]
        assert "private original" not in errors[0]["content"]
        assert all(fact.source_ref != "bad-reference" for fact in result.facts)
        assert not any(entry.get("detail", {}).get("code") == "RESULT_ARCHIVE_UNAVAILABLE"
                       for entry in result.execution_feedback)
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["store", "digest", "deleted"])
def test_optional_reader_does_not_convert_infrastructure_or_integrity_failures(fault):
    class Unavailable(InMemoryStore):
        async def aget(self, *args, **kwargs):
            raise OSError("storage down")

    async def run():
        context, calls = _context(), []
        store = Unavailable() if fault == "store" else InMemoryStore()
        archive = TargetResultArchive(store)
        reference = await archive.save(context, {"content": "original"})
        if fault == "digest":
            await store.aput(archive.namespace(context), reference, {"content": "changed"}, index=False)
        model = ScriptedToolModel(responses=[invocation("read_tool_result", {"reference": reference}, "read")])
        worker = TargetFrameworkAgent(model, _manager(calls), review_model=model,
            review_available_tokens=14200, result_store=store,
            registry=build_default_capability_registry("tenant-a"), system_prompt="Read existing evidence.")
        if fault == "deleted":
            worker._archive = TargetResultArchive(store, subject_fence=lambda _: SimpleNamespace(deleted=True))
        result = await worker(context)
        assert result.reason_code == "RESULT_ARCHIVE_UNAVAILABLE"
        assert model.calls <= 1 and not calls
        assert not result.facts and result.candidate_response is None
    asyncio.run(run())


def test_required_restore_still_raises_for_missing_reference():
    async def run():
        with pytest.raises(ResultReferenceNotFound) as error:
            await TargetResultArchive(InMemoryStore()).load(_context(), "missing")
        assert isinstance(error.value, ResultArchiveError)
        assert not error.value.retryable
    asyncio.run(run())


def test_repeated_invalid_references_keep_existing_sdk_budget():
    context, calls = _context(), []
    model = ScriptedToolModel(responses=[
        invocation("read_tool_result", {"reference": "missing"}, f"read-{i}")
        for i in range(context.work_item.max_steps + 1)])
    worker = TargetFrameworkAgent(model, _manager(calls), review_model=model,
        review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Read evidence.")
    result = asyncio.run(worker(context))
    assert model.calls <= context.work_item.max_steps
    assert result.status.value in {"BLOCKED", "TERMINAL_FAILURE"}
    assert result.reason_code != "RESULT_ARCHIVE_UNAVAILABLE"
    assert not result.facts and not calls


def test_missing_required_resume_artifact_stops_before_any_model_call():
    context, calls = _context(), []
    history = [invocation("request_user_input", {"question": "Which item?"}, "pending"),
        ToolMessage(content="Which item?", tool_call_id="pending", artifact={
            "schema": "agent-result-v1", "reference": "missing",
            "result": {"status": "NEEDS_USER_INPUT"}})]
    context = replace(context, work_item=replace(context.work_item, continuation_of="prior",
                      control=WorkControlBinding("goal", 2)),
                      working_messages=tuple(messages_to_dict(history)))
    model = ScriptedToolModel(responses=[])
    worker = TargetFrameworkAgent(model, _manager(calls), review_model=model,
        review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Resume.")
    result = asyncio.run(worker(context))
    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "RESULT_ARCHIVE_UNAVAILABLE"
    assert model.calls == 0 and not calls
    assert result.execution_feedback[0]["stage"] == "agent_context"
