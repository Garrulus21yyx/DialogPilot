"""Actual reviewer envelopes share archived SDK history editing with the actor."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, RemoveMessage, messages_to_dict
from langgraph.store.memory import InMemoryStore

from application.context_budget import ModelContextBudgetExceeded
from infrastructure.target_context_compaction import ContextCompaction
from infrastructure.target_domain_outcome import DomainOutcomeReview
from infrastructure.target_result_archive import TargetResultArchive
from tests.test_target_framework_agent import ScriptedToolModel, _context


@pytest.mark.parametrize("batch", [1, 7])
@pytest.mark.parametrize("kind", ["COMPLETE", "PREPARE_ACTION", "NEEDS_USER_INPUT"])
@pytest.mark.parametrize("envelope_size", [1, 80])
def test_actor_fit_review_overflow_compacts_once_and_preserves_current_evidence(batch, kind, envelope_size, monkeypatch):
    async def run():
        context = _context()
        pinned = HumanMessage("Inspect only; do not submit. Preserve the explicit order of changes.", id="goal")
        old = HumanMessage(('Historical text with "quotes" and \\paths. ' * 800), id="old")
        calls = [{"id": f"read-{i}", "name": "lookup", "args": {"id": i}} for i in range(batch)]
        results = [ToolMessage(json.dumps({"id": i, "amount_minor": -1346}), tool_call_id=call["id"], id=f"result-{i}")
                   for i, call in enumerate(calls)]
        candidate = {"tool": "prepare_change", "arguments": {"id": "A", "amount_minor": -1346,
            "note": "Keep original payment method. " * envelope_size}}
        pending = AIMessage("Candidate only, not executed.", id="candidate", tool_calls=[
            {"id": "pending", "name": "prepare_change", "args": candidate["arguments"]}])
        messages = [pinned, old, AIMessage("", tool_calls=calls, id="completed-batch"), *results, pending]
        original = messages_to_dict(messages)
        review = DomainOutcomeReview(None, available_tokens=32000,
            business_policy="Follow the source policy. " * envelope_size,
            tools=[SimpleNamespace(name="prepare_change", description="Only prepare. " * envelope_size,
                tool_call_schema={"type": "object", "properties": {"id": {"type": "string"}}})])
        required = review.required_tokens(context=context, messages=messages, kind=kind, candidate=candidate)
        review.available_tokens = required - 3000
        measure = lambda history: (review.required_tokens(context=context, messages=history,
            kind=kind, candidate=candidate), review.available_tokens)
        archive = TargetResultArchive(InMemoryStore())
        model = ScriptedToolModel(responses=[AIMessage("Old investigation recorded. No submission authorized.")])
        compactor = ContextCompaction(model, archive, available_tokens=32000, overhead_tokens=100,
                                     pinned_message=pinned)
        assert compactor.count(messages) < compactor.available
        assert required > review.available_tokens
        update = await compactor.admit({"messages": messages}, SimpleNamespace(context=context), consumer_budget=measure)
        retained = [m for m in update["messages"] if not isinstance(m, RemoveMessage)]
        assert pinned in retained and pending in retained
        assert all(result in retained for result in results)
        assert messages_to_dict(messages) == original
        record, = update["compaction_records"]
        assert record["summarized"] and not record["offloaded_tool_calls"]
        assert (await archive.load(context, record["original_ref"]))["messages"] == original
        captured = []
        async def call(_model, **kwargs):
            captured.append(kwargs)
            return {"accepted": True, "feedback": ""}
        monkeypatch.setattr("infrastructure.target_domain_outcome.structured_call", call)
        assert (await review.assess(context=context, messages=retained, kind=kind, candidate=candidate))["accepted"]
        assert captured[0]["messages"] == review.request_messages(
            context=context, messages=retained, kind=kind, candidate=candidate)
        assert measure(retained)[0] <= measure(retained)[1]
        assert model.calls == 1
        again = await compactor.admit({"messages": retained, "compaction_records": [record]},
                                     SimpleNamespace(context=context), consumer_budget=measure)
        assert not again or not any(r["summarized"] for r in again["compaction_records"])
        assert model.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["provider", "second_rejection"])
@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_post_model_admission_is_checkpointed_before_review_failure(failure, backend, request, monkeypatch):
    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import InMemorySaver
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer, AsyncPostgresCheckpointOwner
    from uuid import uuid4
    from infrastructure.target_agent_middleware import InteractionBoundaryMiddleware
    from infrastructure.target_domain_outcome import DomainOutcomeReviewUnavailable, DomainOutcomeRejected
    from application.orchestration_runtime import AgentContextView
    async def run(saver):
        context = _context()
        pinned = HumanMessage("Only prepare the requested change.", id="goal")
        candidate = AIMessage("The requested investigation is complete.", id="candidate")
        messages = [pinned, HumanMessage("Old context. " * 2500, id="old"),
            AIMessage("", id="read-call", tool_calls=[{"id": "read", "name": "lookup", "args": {}}]),
            ToolMessage("Current state permits the change.", id="read-result", tool_call_id="read")]
        review = DomainOutcomeReview(None, available_tokens=7000, business_policy="Policy", tools=[])
        model = ScriptedToolModel(responses=[AIMessage("Earlier work retained. No action executed.")])
        boundary = InteractionBoundaryMiddleware(review=review)
        compaction = ContextCompaction(model, TargetResultArchive(InMemoryStore()),
            available_tokens=32000, overhead_tokens=100, pinned_message=pinned,
            post_model_budget=boundary.review_budget)
        should_fail = True
        async def call(*_args, **_kwargs):
            if should_fail and failure == "provider":
                raise TimeoutError("review unavailable")
            return {"accepted": not should_fail, "feedback": "Resolve the remaining target choice." if should_fail else ""}
        monkeypatch.setattr("infrastructure.target_domain_outcome.structured_call", call)
        actor = ScriptedToolModel(responses=[candidate])
        def graph_instance():
            return create_agent(actor, tools=[], context_schema=AgentContextView,
                middleware=[boundary, compaction], checkpointer=saver)
        graph = graph_instance()
        config = {"configurable": {"thread_id": uuid4().hex}}
        with pytest.raises(DomainOutcomeReviewUnavailable if failure == "provider" else DomainOutcomeRejected):
            await graph.ainvoke({"messages": messages, "outcome_review_calls": 1 if failure == "second_rejection" else 0},
                                config=config, context=context)
        snapshot = await graph.aget_state(config)
        assert snapshot.values["compaction_records"][0]["summarized"]
        assert candidate in snapshot.values["messages"] and messages[-1] in snapshot.values["messages"]
        assert model.calls == actor.calls == 1
        should_fail = False
        output = await graph_instance().ainvoke(None, config=config, context=context)
        assert output["accepted_outcome"]["kind"] == "COMPLETE"
        assert len(output["compaction_records"]) == 1
        assert model.calls == actor.calls == 1
    if backend == "memory":
        asyncio.run(run(InMemorySaver(serde=target_checkpoint_serializer())))
    else:
        url = request.getfixturevalue("postgres_database_url")
        async def postgres():
            async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                await run(saver)
        asyncio.run(postgres())


def test_irreducible_candidate_does_not_offload_supporting_tool_body():
    async def run():
        context = _context()
        archive = TargetResultArchive(InMemoryStore())
        pinned = HumanMessage("Do not change the payment method.", id="goal")
        content = "required current evidence " * 1500
        ref = await archive.save(context, {"content": content})
        messages = [pinned, AIMessage("", tool_calls=[{"id": "read", "name": "lookup", "args": {}}]),
            ToolMessage(content, tool_call_id="read", artifact={"reference": ref}),
            AIMessage("Proposed completion", id="candidate")]
        review = DomainOutcomeReview(None, available_tokens=2000, business_policy="Policy", tools=[])
        model = ScriptedToolModel(responses=[])
        compactor = ContextCompaction(model, archive, available_tokens=32000, overhead_tokens=100,
                                     pinned_message=pinned)
        original = messages_to_dict(messages)
        with pytest.raises(ModelContextBudgetExceeded) as error:
            await compactor.admit({"messages": messages}, SimpleNamespace(context=context),
                consumer_budget=lambda history: (review.required_tokens(context=context, messages=history,
                    kind="COMPLETE", candidate="Proposed completion"), review.available_tokens))
        assert "2000" in str(error.value)
        assert model.calls == 0 and messages_to_dict(messages) == original
    asyncio.run(run())
