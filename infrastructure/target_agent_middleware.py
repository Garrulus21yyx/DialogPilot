"""Per-call control and context policies for the shared framework Agent."""
from __future__ import annotations

import json
import hashlib

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from application.work_control import WorkControlGuard
from core.framework_models import invoke_model
from infrastructure.target_domain_outcome import DomainOutcomeRejected
from application.execution_progress import advance_progress, observation_key, PROGRESS_FEEDBACK


class ModelInvocationMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        return await invoke_model(handler(request), stage="domain_model")


def model_overhead_tokens(system_message, tools):
    system = system_message if isinstance(system_message, SystemMessage) else SystemMessage(content=system_message or "")
    schemas = [{"name": tool.name, "description": tool.description,
                "schema": tool.tool_call_schema if isinstance(tool.tool_call_schema, dict)
                else tool.tool_call_schema.model_json_schema()} for tool in tools]
    return count_tokens_approximately([system, HumanMessage(content=json.dumps(schemas, ensure_ascii=False))])


def tool_observation_digest(result):
    from application.knowledge_tool_contract import knowledge_progress_identity
    observation = {key: result.get(key) for key in ("data", "error", "status", "effect_status")}
    if result.get("authority") == "knowledge.active_source":
        observation["data"] = knowledge_progress_identity(result.get("data"))
    return hashlib.sha256(json.dumps(observation, sort_keys=True, default=str).encode()).hexdigest()


def tool_observation_digests(result):
    from application.knowledge_tool_contract import knowledge_outcome, knowledge_progress_identity
    from application.agent_result import AgentResultStatus
    if (result.get("authority") == "knowledge.active_source"
            and knowledge_outcome(result.get("data"))[0] is AgentResultStatus.SUCCEEDED):
        identity = knowledge_progress_identity(result["data"])
        return [tool_observation_digest({**result, "authority": None,
            "data": {**identity, "items": [item]}}) for item in identity["items"]]
    return [tool_observation_digest(result)]


def tool_observation_uses_arguments(result):
    from application.knowledge_tool_contract import knowledge_outcome
    from application.agent_result import AgentResultStatus
    return not (result.get("authority") == "knowledge.active_source"
                and knowledge_outcome(result.get("data"))[0] is AgentResultStatus.SUCCEEDED)


class ProgressState(AgentState):
    observed_results: list[str]
    stagnant_rounds: int
    progress_warning: bool
    progress_blocked: bool
    consumed_progress_calls: list[str]
    causal_read_lineage: list[dict]


class AgentProgressMiddleware(AgentMiddleware):
    """Bound repeated observations, not legitimate new tool evidence.

    Native graph state owns the counters. Two rounds without new observations
    offer one model-directed recovery round; continued stagnation ends this
    segment. Fresh user input starts a new segment, including explicit refresh.
    """

    state_schema = ProgressState

    def __init__(self, trace_sink=None):
        self.trace_sink = trace_sink

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        batch = []
        lineage = []
        consumed = set(state.get("consumed_progress_calls", ()))
        calls = {call["id"]: call for message in state["messages"] if isinstance(message, AIMessage)
                 for call in message.tool_calls}
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            artifact = message.artifact or {}
            if artifact.get("schema") == "agent-result-v1":
                # Interactions/actions are governed by their own boundary.
                continue
            if message.tool_call_id in consumed:
                continue
            consumed.add(message.tool_call_id)
            result = artifact.get("result", {})
            digests = artifact.get("observation") or tool_observation_digests(result if result else {
                "data": message.content, "status": message.status})
            # Old checkpoints contain one digest; new knowledge observations
            # carry one per evidence item, so recombining old hits is not novelty.
            if isinstance(digests, str):
                digests = [digests]
            # Successful knowledge novelty is per evidence, not query rephrasing.
            uses_arguments = artifact.get("observation_uses_arguments", tool_observation_uses_arguments(result))
            arguments = calls.get(message.tool_call_id, {}).get("args") if uses_arguments else None
            identities = [observation_key(message.name, arguments, digest) for digest in digests]
            batch.extend(identities)
            source_call_ids = tuple(dict.fromkeys(
                str(call_id) for call_id in result.get("causal_source_call_ids", ()) if str(call_id)
            ))
            lineage.extend({
                "read_identity": identity,
                "observation_read_call_id": str(message.tool_call_id),
                "source_business_call_ids": source_call_ids,
            } for identity in identities if source_call_ids)
        if not batch:
            return None
        prior = set(state.get("observed_results", ()))
        update = {**advance_progress(state, batch), "consumed_progress_calls": sorted(consumed),
                  "causal_read_lineage": lineage}
        if self.trace_sink is not None:
            context = runtime.context
            item = context.work_item
            decision = (
                "BLOCK" if update["progress_blocked"] else
                "WARN" if update["progress_warning"] else
                "ALLOW_FIRST_REPLAY" if all(key in prior for key in batch) else "ALLOW_NEW_EVIDENCE"
            )
            lineage_by_identity = {entry["read_identity"]: entry for entry in lineage}
            for identity in dict.fromkeys(batch):
                source = lineage_by_identity.get(identity, {})
                self.trace_sink.record_causal_event(
                    "READ_REPLAYED" if identity in prior else "READ_OBSERVED",
                    owner="agent_progress", turn_id=str(
                        context.trusted_context.get("invocation_key") or item.work_item_id
                    ), work_item_id=item.work_item_id,
                    control_id=item.control.control_id if item.control else None,
                    control_revision=item.control.revision if item.control else None,
                    read_identity=identity, guard_decision=decision,
                    stagnant_rounds=update["stagnant_rounds"],
                    observation_read_call_id=source.get("observation_read_call_id"),
                    source_business_call_ids=json.dumps(
                        source.get("source_business_call_ids", ()), separators=(",", ":")
                    ),
                )
        if update["progress_blocked"]:
            return {**update, "jump_to": "end"}
        if update["progress_warning"]:
            update["messages"] = [HumanMessage(content=PROGRESS_FEEDBACK)]
        return update

    async def aafter_model(self, state, runtime):
        lineage = state.get("causal_read_lineage", ())
        message = state["messages"][-1] if state.get("messages") else None
        if self.trace_sink is not None and lineage and isinstance(message, AIMessage):
            context = runtime.context
            item = context.work_item
            for call in message.tool_calls:
                for source in lineage:
                    self.trace_sink.record_causal_event(
                        "READ_INFORMED_TOOL_EMISSION",
                        owner="agent_progress",
                        turn_id=str(context.trusted_context.get("invocation_key") or item.work_item_id),
                        work_item_id=item.work_item_id,
                        control_id=item.control.control_id if item.control else None,
                        control_revision=item.control.revision if item.control else None,
                        read_identity=source["read_identity"],
                        observation_read_call_id=source["observation_read_call_id"],
                        source_business_call_ids=json.dumps(
                            source["source_business_call_ids"], separators=(",", ":")
                        ),
                        emitted_business_call_id=str(call["id"]),
                        action_name=str(call["name"]),
                    )
        return {"causal_read_lineage": []} if lineage else None


class OutcomeState(AgentState):
    outcome_review_calls: int
    accepted_outcome: dict
    outcome_feedback: list[dict]


class InteractionBoundaryMiddleware(AgentMiddleware):
    """Accept a goal-bound handback before ending or executing an interaction."""

    state_schema = OutcomeState
    max_rejections = 2

    def __init__(self, action_tools=(), *, review, action_rules=None):
        self.action_tools = frozenset(action_tools)
        self.review = review
        self.action_rules = dict(action_rules or {})

    def proposed_outcome(self, message):
        calls = message.tool_calls if isinstance(message, AIMessage) else ()
        proposals = [call for call in calls if call["name"] in self.action_tools]
        interactions = {"request_user_input": "NEEDS_USER_INPUT", "report_blocked": "BLOCKED"}
        if len(calls) > 1 and any(call["name"] in interactions for call in calls):
            return None
        if calls and not proposals and not (len(calls) == 1 and calls[0]["name"] in interactions):
            return None
        kind = "PREPARE_ACTION" if proposals else interactions[calls[0]["name"]] if calls else "COMPLETE"
        candidate = ({"tool": proposals[0]["name"], "arguments": proposals[0]["args"]}
                     if proposals else calls[0]["args"] if calls else message.text)
        if len(proposals) > 1:
            candidate = {"actions": [{"tool": call["name"], "arguments": call["args"]}
                                     for call in proposals]}
        return kind, candidate

    def review_budget(self, messages, context):
        outcome = self.proposed_outcome(messages[-1])
        if outcome is None:
            return None
        kind, candidate = outcome
        if kind != "PREPARE_ACTION":
            return None
        return lambda history: (self.review.required_tokens(context=context,
            messages=history, kind=kind, candidate=candidate), self.review.available_tokens)

    @staticmethod
    def _prepared(state, context):
        historical_calls = {entry["data"].get("tool_call_id") for entry in context.working_messages
                            if entry.get("type") == "tool"}
        return any(record.get("pending_action") and call_id not in historical_calls
                   for call_id, record in state.get("tool_observations", {}).items())

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        message = state["messages"][-1]
        calls = message.tool_calls if isinstance(message, AIMessage) else ()
        proposals = [call for call in calls if call["name"] in self.action_tools]
        if len(calls) > 1 and any(call["name"] in {"request_user_input", "report_blocked"} for call in calls):
            return {"messages": [ToolMessage(
                content="No tools in this batch were executed. Make one interaction call, or perform evidence calls first and ask afterwards.",
                tool_call_id=call["id"], name=call["name"], status="error") for call in calls],
                "jump_to": "model"}
        outcome = self.proposed_outcome(message)
        if outcome is None:
            return None
        kind, candidate = outcome
        if kind != "PREPARE_ACTION":
            # This records a handback, not a semantic attestation. Tool results
            # and requirements remain the authority for the resulting task state.
            return {"accepted_outcome": {"kind": kind, "message_id": message.id,
                "tool_call_id": calls[0]["id"] if calls else None}}
        review_calls = state.get("outcome_review_calls", 0)
        # Accepted proposals may encounter a preparation failure. They consume
        # model/tool budget, not semantic correction budget. Persisted feedback
        # is the authority, including checkpoints written before this change.
        rejections = sum(entry.get("accepted") is False
                         for entry in state.get("outcome_feedback", ()))
        if rejections >= self.max_rejections:
            raise DomainOutcomeRejected("domain_outcome_correction_budget_exhausted")
        assessment = None
        compatibility = None
        if kind == "PREPARE_ACTION":
            from application.operation_plan import OperationPlanError, validate_operation_plan
            from application.action_compatibility import ActionCompatibilityError, validate_action_compatibility
            try:
                for proposal in candidate.get("actions", (candidate,)):
                    if "operation_plan" in proposal["arguments"]:
                        validate_operation_plan(proposal["arguments"]["operation_plan"],
                            selected_tool=proposal["tool"], allowed_tools=self.action_rules.keys() | self.action_tools)
                compatibility = validate_action_compatibility(candidate, self.action_rules)
            except (OperationPlanError, ActionCompatibilityError) as exc:
                assessment = {"accepted": False, "feedback": str(exc), "repair_owner": "domain",
                    "reason_code": getattr(exc, "code", "OPERATION_PLAN_INVALID"), "model_called": False}
        if assessment is None:
            assessment = await self.review.assess(context=runtime.context,
                messages=state["messages"], kind=kind, candidate=candidate)
            review_calls += int(assessment.get("model_called", True))
        if compatibility is not None:
            assessment = {**assessment, "compatibility_check": compatibility}
        if not assessment["accepted"] and assessment.get("repair_owner") == "conversation":
            from infrastructure.target_domain_outcome import DomainAssignmentRejected
            raise DomainAssignmentRejected(assessment["feedback"])
        feedback = [*state.get("outcome_feedback", ()), {"kind": kind, **assessment}]
        update = {"outcome_review_calls": review_calls, "outcome_feedback": feedback,
                  "accepted_outcome": {}}
        if assessment["accepted"]:
            return {**update, "accepted_outcome": {"kind": kind, "message_id": message.id,
                "tool_call_id": proposals[0]["id"] if proposals else calls[0]["id"] if calls else None}}
        if rejections + 1 >= self.max_rejections:
            # A typed failure is retained by the adapter; nothing is relabelled
            # complete and no rejected input tool gets a durable observation.
            raise DomainOutcomeRejected(assessment["feedback"])
        correction = "Internal task review (not a user reply or approval): " + assessment["feedback"]
        return {**update, "jump_to": "model", "messages": ([ToolMessage(
            content=correction, tool_call_id=call["id"], name=call["name"], status="error") for call in calls]
            if calls else [HumanMessage(content=correction)])}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        # This hook runs after the complete tool batch and result persistence.
        # The proposal is the handback; customer prose belongs to Publication.
        if self._prepared(state, runtime.context):
            return {"jump_to": "end"}
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            artifact = message.artifact
            if (isinstance(artifact, dict) and artifact.get("schema") == "agent-result-v1"
                    and artifact.get("result", {}).get("producer_version") == "domain-interaction-v1"):
                if state.get("accepted_outcome", {}).get("tool_call_id") != message.tool_call_id:
                    raise DomainOutcomeRejected("domain_interaction_has_no_accepted_outcome")
                return {"jump_to": "end"}
        return None


class WorkControlMiddleware(AgentMiddleware):
    def __init__(self, guard: WorkControlGuard | None) -> None:
        self.guard = guard

    def check(self, context) -> None:
        if self.guard is not None:
            self.guard.ensure_current(context.work_item, context.trusted_context)

    async def awrap_model_call(self, request, handler):
        self.check(request.runtime.context)
        response = await handler(request)
        self.check(request.runtime.context)
        return response

    async def awrap_tool_call(self, request, handler):
        self.check(request.runtime.context)
        response = await handler(request)
        self.check(request.runtime.context)
        return response


class AgentContextMiddleware(AgentMiddleware):
    """Validate the complete provider view against the configured model budget.

    A byte prefix is not a semantic projection of a structured tool result. Keep
    in-budget payloads intact; genuine overflow has an explicit typed outcome.
    """

    def __init__(self, budget: ContextBudgetManager) -> None:
        self.budget = budget

    async def awrap_model_call(self, request, handler):
        messages = list(request.messages)
        # Schemas and the system message consume the same input window as history.
        overhead = model_overhead_tokens(request.system_message, request.tools)
        available = self.budget.available_tokens - overhead

        required = count_tokens_approximately(messages)
        if required > available:
            raise ModelContextBudgetExceeded(required + overhead, self.budget.available_tokens)
        return await handler(request.override(messages=messages))
