"""Framework-backed execution for open, read-only domain goals.

The parent WorkPlan remains the scheduling authority.  This adapter delegates
only the bounded model/tool loop to LangChain's standard Agent graph while the
existing ToolManager continues to own discovery, authorization, identity,
execution and receipts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import nullcontext
from dataclasses import asdict, replace
from typing import Annotated, Any, Mapping

from pydantic import Field

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from infrastructure.target_model_context import delegated_task_content
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from langchain_core.tools import StructuredTool, ToolException
from langchain.tools import ToolRuntime
from langgraph.errors import GraphRecursionError
from langfuse import propagate_attributes
from langgraph.store.base import BaseStore
from langchain_core.messages.utils import count_tokens_approximately
from infrastructure.target_result_archive import TargetResultArchive, ResultArchiveError, result_pointer, MAX_RESULT_PAGE_CHARS
from infrastructure.target_result_archive import ResultReferenceNotFound
from infrastructure.target_context_compaction import ToolResultPersistence, ContextCompaction

from application.knowledge_tool_contract import tool_domain_outcome
from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    MissingInputSpec,
)
from application.capability_registry import (
    CapabilityEffect,
    CapabilityRegistryBundle,
)
from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from application.orchestration_runtime import AgentContextView, WorkExecutor
from application.work_item import ArgumentValue, ControlMode
from application.work_control import WorkControlGuard, WorkSuperseded
from infrastructure.target_agent_result_adapter import (
    fact_from_tool_result,
    merge_facts,
    framework_artifact,
    restore_framework_artifact,
    resolved_working_messages,
)
from infrastructure.target_agent_middleware import AgentContextMiddleware, WorkControlMiddleware, InteractionBoundaryMiddleware, AgentProgressMiddleware, ModelInvocationMiddleware, model_overhead_tokens
from core.framework_models import ModelInvocationError
from core.skill_loader import SkillManager
from infrastructure.target_skill_tools import skill_reader
from infrastructure.target_action_preparation import TargetActionPreparation
from application.agent_instructions import domain_instructions
from infrastructure.target_domain_outcome import (
    DomainOutcomeReview, DomainOutcomeRejected, DomainOutcomeReviewUnavailable, DomainAssignmentRejected,
)
from mcp.tool_manager import MCPToolManager, ToolCallStatus, ToolResult

logger = logging.getLogger(__name__)


class TargetFrameworkAgent:
    """Execute one delegated read goal through a governed framework Agent."""

    version = "target-framework-agent-v12-scoped-evidence-context"

    def __init__(
        self,
        model: Any,
        tool_manager: MCPToolManager,
        *,
        review_model: Any,
        review_available_tokens: int,
        result_store: BaseStore,
        result_subject_fence=None,
        registry: CapabilityRegistryBundle,
        system_prompt: str,
        skill_executors: Mapping[str, WorkExecutor] | None = None,
        skill_manager: SkillManager | None = None,
        context_budget: ContextBudgetManager | None = None,
        control_guard: WorkControlGuard | None = None,
        callbacks: tuple = (),
        trace_sink=None,
    ) -> None:
        self._model = model
        self._review_model = review_model
        self._review_available_tokens = review_available_tokens
        self._tool_manager = tool_manager
        self._registry = registry
        self._system_prompt = str(system_prompt).strip()
        self._skill_executors = dict(skill_executors or {})
        self._skill_manager = skill_manager
        self._context_budget = context_budget or ContextBudgetManager()
        self._control_guard = control_guard
        self._callbacks = callbacks
        self._trace_sink = trace_sink
        self._archive = TargetResultArchive(result_store, subject_fence=result_subject_fence)

    async def __call__(self, context: AgentContextView) -> AgentResult:
        item = context.work_item
        if self._control_guard is not None and not self._control_guard.is_current(
            item, context.trusted_context,
        ):
            return self._control_guard.superseded_result(item)
        if item.control_mode is not ControlMode.DELEGATED:
            return self._failure(context, "AGENT_REQUIRES_DELEGATED_WORK")
        if item.effect is not CapabilityEffect.READ:
            return self._failure(context, "AGENT_WRITE_REQUIRES_WORKFLOW")
        if item.owner_agent not in {agent.agent_id for agent in self._registry.agents}:
            return self._failure(context, "DOMAIN_AGENT_NOT_REGISTERED")
        if item.skill_hint is not None:
            executor = self._skill_executors.get(item.skill_hint)
            if executor is None:
                return self._failure(context, "SKILL_EXECUTOR_NOT_REGISTERED")
            return await executor(context)

        try:
            history = await resolved_working_messages(context, self._archive)
            tools = self._tools(context)
            system = self._system(context)
            overhead = model_overhead_tokens(system, tools)
            prompt = await self._prepare_prompt(context, overhead_tokens=overhead)
        except ModelContextBudgetExceeded as exc:
            return self._failure(context, "CONTEXT_BUDGET_EXCEEDED", error=exc, stage="agent_context")
        except ResultArchiveError as exc:
            return self._failure(context, "RESULT_ARCHIVE_UNAVAILABLE", retryable=exc.retryable, error=exc, stage="agent_context")
        except ValueError as exc:
            return self._failure(context, "INVALID_AGENT_CAPABILITY_ENVELOPE", error=exc, stage="agent_context")

        # Replace application-owned task envelopes for this execution; preserve
        # native conversation and tool messages for framework recovery.
        from infrastructure.target_model_context import delegated_working_input
        working, pinned = delegated_working_input(history, prompt, item.fingerprint)
        review = DomainOutcomeReview(self._review_model, callbacks=self._callbacks,
            available_tokens=self._review_available_tokens,
            business_policy=self._system_prompt, tools=tools,
            archive=self._archive,
            registered_action_refs=tuple(action.ref for action in self._registry.actions
                                        if action.owner_agent == item.owner_agent))
        boundary = InteractionBoundaryMiddleware(("prepare_" + tool_id for ref in item.allowed_actions
            for tool_id in self._registry.action(ref).allowed_tool_ids), review=review,
            action_rules={"prepare_" + tool: action.state_transition
                for action in self._registry.actions for tool in action.allowed_tool_ids})
        compaction = ContextCompaction(self._model, self._archive,
            available_tokens=self._context_budget.available_tokens,
            overhead_tokens=overhead, pinned_message=pinned, max_summary_calls=item.max_steps,
            post_model_budget=boundary.review_budget, trace_sink=self._trace_sink)
        graph = create_agent(
            self._model,
            tools,
            system_prompt=system,
            name=f"{item.owner_agent}_agent",
            context_schema=AgentContextView,
            middleware=[
                WorkControlMiddleware(self._control_guard),
                ToolResultPersistence(self._archive, max_inline_tokens=max(1,
                    int((self._context_budget.available_tokens - overhead) * .25))),
                boundary,
                AgentProgressMiddleware(self._trace_sink),
                compaction,
                AgentContextMiddleware(self._context_budget),
                ModelCallLimitMiddleware(thread_limit=item.max_steps, exit_behavior="error"),
                ToolCallLimitMiddleware(thread_limit=item.max_steps, exit_behavior="error"),
                ModelInvocationMiddleware(),
            ],
        )
        config = {
            "recursion_limit": item.max_steps * 8 + 10,
            "callbacks": list(self._callbacks),
            "metadata": {
                "work_item_id": item.work_item_id,
                "owner_agent": item.owner_agent,
                "control_id": item.control.control_id if item.control else None,
                "revision": item.control.revision if item.control else None,
                "invocation_key": context.trusted_context.get("invocation_key"),
                "langfuse_session_id": context.trusted_context.get("conversation_id"),
            },
        }
        from application.agent_working_state import working_state
        initial = {**working_state(context.working_state), "messages": working}
        output = initial
        failure = None
        try:
            with (propagate_attributes(session_id=context.trusted_context.get("conversation_id"))
                  if self._callbacks else nullcontext()):
                async with asyncio.timeout(item.timeout_seconds):
                    # Native graph state streaming preserves the last completed step
                    # when a later model/tool step fails. No second loop or recorder.
                    async for output in graph.astream(
                        initial,
                        config=config,
                        context=context,
                        stream_mode="values",
                    ):
                        pass
        except WorkSuperseded:
            return self._control_guard.superseded_result(item)
        except (GraphRecursionError, ModelCallLimitExceededError, ToolCallLimitExceededError) as exc:
            failure = self._failure(context, "AGENT_STEP_BUDGET_EXCEEDED", error=exc)
        except ModelContextBudgetExceeded as exc:
            failure = self._failure(context, "CONTEXT_BUDGET_EXCEEDED", error=exc)
        except ResultArchiveError as exc:
            failure = self._failure(context, "RESULT_ARCHIVE_UNAVAILABLE", retryable=exc.retryable, error=exc)
        except TimeoutError as exc:
            failure = self._failure(
                context, "AGENT_EXECUTION_TIMEOUT", retryable=True, error=exc,
            )
        except ModelInvocationError as exc:
            failure = self._failure(context, f"MODEL_INVOCATION_FAILED:{exc.stage}:{exc.error_type}",
                                    retryable=exc.retryable, error=exc)
        except DomainOutcomeRejected as exc:
            failure = self._failure(context, "DOMAIN_OUTCOME_REJECTED", error=exc, stage="domain_outcome")
        except DomainAssignmentRejected as exc:
            failure = replace(self._failure(context, "ASSIGNMENT_REPAIR_REQUIRED", error=exc,
                stage="assignment_review"), assignment_issue=str(exc))
        except DomainOutcomeReviewUnavailable as exc:
            from core.framework_models import retryable_model_error
            failure = self._failure(context, "DOMAIN_OUTCOME_REVIEW_UNAVAILABLE",
                retryable=(exc.__cause__.retryable if isinstance(exc.__cause__, ModelInvocationError)
                           else retryable_model_error(exc.__cause__)), error=exc, stage="domain_outcome")
        except Exception as exc:
            failure = self._failure(
                context,
                f"AGENT_INTERNAL_FAILURE:{type(exc).__name__}",
                retryable=False, error=exc,
            )
        if self._control_guard is not None and not self._control_guard.is_current(
            item, context.trusted_context,
        ):
            return self._control_guard.superseded_result(item)
        # Historical artifacts remain in the working conversation, but only the
        # current segment can request another interaction or propose an action.
        history_ids = {message.id for message in history}
        history_calls = {message.tool_call_id for message in history if isinstance(message, ToolMessage)}
        current_messages = [
            message for message in output.get("messages", [])
            if (message.tool_call_id not in history_calls if isinstance(message, ToolMessage)
                else message.id not in history_ids)
        ]
        observed = []
        feedback = list(failure.execution_feedback) if failure else []
        for record in output.get("tool_observations", {}).values():
            try:
                if "inline_artifact" in record:
                    artifact = record["inline_artifact"]
                else:
                    artifact = (await self._archive.load(context, record["reference"]))["artifact"]
                restored = restore_framework_artifact(artifact)
                observed.append(restored)
                if isinstance(restored, ToolResult):
                    feedback.append({"stage": "tool", "tool": restored.tool_name,
                        "call_id": restored.call_id, "status": restored.status,
                        "success": restored.success, "error": restored.error,
                        "effect_status": restored.effect_status})
            except ResultArchiveError as exc:
                failure = self._failure(context, "RESULT_ARCHIVE_UNAVAILABLE", retryable=exc.retryable,
                                        error=exc, stage="agent_result_restore")
                feedback.extend(failure.execution_feedback)
            except (ValueError, KeyError, TypeError) as exc:
                failure = self._failure(context, "RESULT_ARTIFACT_INVALID", error=exc, stage="agent_result_restore")
                feedback.extend(failure.execution_feedback)
        result = _adapt_framework_result(
            context,
            tuple(observed),
            self.version,
            accepted_outcome=output.get("accepted_outcome"),
            candidate_response=next((message.text for message in reversed(current_messages)
                if isinstance(message, AIMessage) and not message.tool_calls), None),
            allowed_authorities={
                tool_id: self._registry.tool(tool_id).authority
                for tool_id in item.allowed_tools
            },
        )
        if output.get("archive_failed"):
            errors = [record["archive_error"] for record in output.get("tool_observations", {}).values()
                      if "archive_error" in record]
            failure = self._failure(context, "RESULT_ARCHIVE_UNAVAILABLE",
                retryable=bool(errors) and all(error["retryable"] for error in errors),
                stage="agent_result_archive", related_errors=errors)
            feedback.extend(failure.execution_feedback)
        if output.get("progress_blocked") and result.pending_action is None:
            result = replace(result, status=AgentResultStatus.BLOCKED,
                reason_code="AGENT_NO_PROGRESS", retryable=False, candidate_response=None)
        if failure is not None and result.pending_action is None:
            result = replace(result, status=failure.status, reason_code=failure.reason_code,
                retryable=failure.retryable, candidate_response=None, pending_action=None, additional_actions=(),
                missing_inputs=(), assignment_issue=failure.assignment_issue)
        elif failure is not None:
            result = replace(result, candidate_response=None)
        working = list(output.get("messages", []))
        if failure is not None:
            pending_calls = []
            if working and isinstance(working[-1], AIMessage) and working[-1].tool_calls:
                pending_calls = working.pop().tool_calls
            diagnostic = {
                "reason_code": failure.reason_code, "retryable": failure.retryable,
                "uncompleted_tool_batch": pending_calls,
                "instruction": "Retain completed evidence. The uncompleted batch has no confirmed result; do not infer success. Reassess the remaining objective before continuing.",
            }
            feedback.append(diagnostic)
            working.append(HumanMessage(content=json.dumps({"execution_feedback": diagnostic}, ensure_ascii=False)))
        feedback.extend({"stage": "domain_outcome", **review} for review in output.get("outcome_feedback", ()))
        return replace(result, working_messages=tuple(messages_to_dict(working)),
                       working_state=working_state(output), execution_feedback=tuple(feedback))

    def _tools(self, context: AgentContextView) -> list[StructuredTool]:
        item = context.work_item
        runtime_agent = self._registry.agent(item.owner_agent).execution_principal
        definitions = self._tool_manager.tools_for_agent(
            runtime_agent,
            allowed_tool_ids=item.allowed_tools,
        )
        tools = [
            self._atomic_tool(definition, context.trusted_context)
            for definition in definitions
        ]
        for skill_id in item.allowed_skills:
            tools.append(self._skill_tool(skill_id))
        # Conversation state already owns one prepared decision. Read-only
        # assistance remains available; another proposal cannot replace it.
        action_refs = () if context.pending_approval else item.allowed_actions
        for action_ref in action_refs:
            tools.append(self._action_tool(action_ref))
        if not tools:
            raise ValueError("delegated Agent has no executable capability")
        packages = self._skill_manager.for_agent(item.owner_agent) if self._skill_manager else ()
        if packages:
            tools.append(skill_reader(packages))
        async def read_tool_result(reference: str, runtime: ToolRuntime[AgentContextView, dict],
                                   offset: Annotated[int, Field(ge=0)] = 0, limit: Annotated[int | None, Field(ge=1, le=MAX_RESULT_PAGE_CHARS)] = None, evidence_id: str | None = None):
            try:
                batch = next((message.tool_calls for message in reversed(runtime.state.get("messages", []))
                              if isinstance(message, AIMessage) and message.tool_calls), ())
                # Concurrent reads share a working allowance. A single fitting
                # result need not paginate; N simultaneous reads cannot each
                # reserve a quarter of the same window.
                allowance = max(1, (self._context_budget.available_tokens - reader_overhead)
                                // max(4, 2 * len(batch)))
                return await self._archive.read(runtime.context, reference, offset, limit, evidence_id,
                    max_tokens=allowance)
            except ResultReferenceNotFound as exc:
                raise ToolException(
                    "ARCHIVE_REFERENCE_NOT_FOUND: No matching result or evidence in this task. "
                    "Use an archive reference supplied by a tool result or working-history pointer, "
                    "and an evidence_id from its directory. A fact source_ref is not an archive address. "
                    "If no archive pointer was supplied, use the available evidence or another authorized capability."
                ) from exc
        reader = StructuredTool.from_function(coroutine=read_tool_result,
            name="read_tool_result",
            handle_tool_error=True,
            description="Read an archived result in this task, without rerunning business tools. Omit limit to read the whole result when it fits the working allowance; oversized results return next_offset. Use evidence_id for a relevant knowledge item, or offset/limit for an explicit range. Continue only when required content remains unread.")
        exposed = [*tools, *self._interaction_tools(), reader]
        reader_overhead = model_overhead_tokens(self._system(context), exposed)
        names = [tool.name for tool in exposed]
        if len(names) != len(set(names)):
            raise ValueError("agent tool names must be unique after action preparation binding")
        return exposed

    def _interaction_tools(self) -> list[StructuredTool]:
        async def request_user_input(question: Annotated[str, Field(min_length=1)], runtime: ToolRuntime[AgentContextView, dict]):
            item = runtime.context.work_item
            result = AgentResult(item.work_item_id, item.owner_agent,
                AgentResultStatus.NEEDS_USER_INPUT, "DOMAIN_INPUT_REQUIRED", "domain-interaction-v1",
                missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                    "DOMAIN_INPUT_REQUIRED", "string", question),))
            return question, framework_artifact(result)

        async def report_blocked(reason: Annotated[str, Field(min_length=1)], runtime: ToolRuntime[AgentContextView, dict],
                                 needs_reassignment: bool = False):
            item = runtime.context.work_item
            result = AgentResult(item.work_item_id, item.owner_agent,
                AgentResultStatus.TERMINAL_FAILURE if needs_reassignment else AgentResultStatus.BLOCKED,
                "DOMAIN_ASSIGNMENT_REPAIR_REQUIRED" if needs_reassignment else "DOMAIN_OBJECTIVE_BLOCKED",
                "domain-interaction-v1", candidate_response=reason,
                assignment_issue=reason if needs_reassignment else None)
            return reason, framework_artifact(result)

        return [StructuredTool.from_function(coroutine=handler, name=name,
            description=description, response_format="content_and_artifact")
            for handler, name, description in (
                (request_user_input, "request_user_input",
                 "Ask only for unresolved information or choices the user must supply. Do not ask the user to repeat a stated request or combine a missing choice with permission to execute. Approval of prepared parameters belongs to the runtime, not this tool. Supply the concise customer-facing question, without promises, policy explanations or claims of completed actions. A single question is published without rewriting; runtime binds the answer. Ends this segment."),
                (report_blocked, "report_blocked",
                 "Explain why the objective cannot proceed. Set needs_reassignment only when another domain capability or a revised task assignment is needed; explain what is missing so the conversation planner can reassign the remaining work. Leave it false for a final business restriction or user refusal. Missing user information belongs to request_user_input; unknown write outcomes belong to runtime reconciliation. Ends this segment without claiming completion."))]

    def _action_tool(self, action_ref):
        action = self._registry.action(action_ref)
        if len(action.allowed_tool_ids) != 1:
            raise ValueError("action must pin one write tool")
        definition, = self._tool_manager.tools_for_agent(
            self._registry.agent(action.owner_agent).execution_principal,
            allowed_tool_ids=action.allowed_tool_ids,
        )
        schema = json.loads(json.dumps(definition.schema))
        from application.operation_plan import operation_plan_schema, validate_operation_plan
        if "operation_plan" in schema.get("properties", {}):
            raise ValueError("business tool argument conflicts with preparation operation_plan")
        planning_names = {"prepare_" + tool for registered in self._registry.actions
                          for tool in registered.allowed_tool_ids}
        schema.setdefault("properties", {})["operation_plan"] = operation_plan_schema(planning_names)
        if action.preparation:
            field = action.preparation.target_version_argument
            schema.get("properties", {}).pop(field, None)
            schema["required"] = [key for key in schema.get("required", ()) if key != field]
        preparation = TargetActionPreparation(self._registry, self._tool_manager, self._control_guard)

        async def propose(runtime: ToolRuntime, **arguments):
            plan = None
            if "operation_plan" in arguments:
                plan = arguments.pop("operation_plan")
                validate_operation_plan(plan, selected_tool="prepare_" + definition.name,
                    allowed_tools=planning_names)
            result = await preparation.prepare(runtime.context, action.ref, arguments, runtime.tool_call_id,
                                               operation_plan=plan)
            feedback = ("Action prepared, NOT executed. This worker segment ends here. The conversation layer explains this proposal and manages approval, bound to these exact parameters. The complete remaining objective is retained for continuation; other operations have not been performed."
                        if result.pending_action else result.reason_code)
            return feedback, framework_artifact(result)

        return StructuredTool.from_function(
            coroutine=propose, name="prepare_" + definition.name,
            description=("Prepare a proposal only; this tool does not execute the business action. "
                "Call once all required choices are known, before requesting approval. "
                "Prepare independent ready operations together in one tool batch for one confirmation. "
                "For later writes not ready in this batch, include operation_plan covering remaining assigned changes, "
                "with evidence-based preconditions/effects and dependencies. This tool call is the ready current action: "
                "describe it in current without repeating its tool, target or step ID. Describe later actions in remaining_steps; "
                "their depends_on may reference current or another remaining step ID. If a prerequisite write is still needed, prepare that action first. "
                "A single write needs no separate plan. If no feasible ordering exists, use request_user_input "
                "to resolve the actual tradeoff before preparing anything; do not promise later impossible actions. "
                "The conversation layer presents the prepared set and collects approval; the runtime executes each member. "
                "Operation: " + definition.name + ". Use the supplied argument schema and business evidence. "
                + ("Resource-state rule: " + json.dumps(asdict(action.state_transition)) + ". "
                   if action.state_transition else "") +
                "Runtime collects execution confirmation for this proposal. "
                "Business prerequisites and effects are in business_operation_reference under the operation name."),
            args_schema=schema, infer_schema=False, response_format="content_and_artifact",
        )

    def _atomic_tool(self, definition, trusted_context=None):
        schema = definition.model_input_schema(trusted_context)

        async def execute(runtime: ToolRuntime, **arguments):
            context = runtime.context
            runtime_agent = self._registry.agent(context.work_item.owner_agent).execution_principal
            if self._control_guard is not None:
                self._control_guard.ensure_current(
                    context.work_item, context.trusted_context,
                )
            refresh = arguments.pop("_refresh", False) if definition.task_read_reuse else False
            result = await self._tool_manager.execute_for_agent(
                definition.name,
                dict(arguments),
                agent_type=runtime_agent,
                call_id=runtime.tool_call_id,
                context={**context.trusted_context, "work_item_id": context.work_item.work_item_id},
                allowed_tool_ids=context.work_item.allowed_tools,
                refresh=refresh,
                # One cache owner for this call. Explicit refresh/invalidation
                # must not fall through to a second, process-local old snapshot.
                use_cache=definition.task_read_reuse is None,
            )
            if self._control_guard is not None:
                self._control_guard.ensure_current(
                    context.work_item, context.trusted_context,
                )
            artifact = framework_artifact(result)
            return _tool_output(result), artifact

        return StructuredTool.from_function(
            coroutine=execute,
            name=definition.name,
            description=definition.description + (
                " The runtime reuses authorized valid results across this conversation, preserving original observation time. "
                "Use existing results to advance; ask for _refresh only for an explicit refresh request or known changed conditions."
                if definition.task_read_reuse else ""),
            args_schema=schema,
            infer_schema=False,
            response_format="content_and_artifact",
        )

    def _skill_tool(self, skill_id):
        definition = self._registry.skill(skill_id)
        executor = self._skill_executors.get(skill_id)
        if executor is None:
            raise ValueError(f"Skill executor is not registered: {skill_id}")
        properties = {
            name: {"type": "string"}
            for name in (*definition.required_arguments, *definition.optional_arguments)
        }

        async def execute(runtime: ToolRuntime, **arguments):
            context = runtime.context
            if self._control_guard is not None:
                self._control_guard.ensure_current(
                    context.work_item, context.trusted_context,
                )
            allowed = set((*definition.required_arguments, *definition.optional_arguments))
            if set(arguments) - allowed:
                raise ValueError("skill arguments exceed Registry schema")
            merged = {
                argument.name: argument.value
                for argument in context.work_item.arguments
                if argument.name in allowed
            }
            merged.update(arguments)
            if set(definition.required_arguments) - set(merged):
                raise ValueError("skill required arguments are missing")
            derived = replace(
                context.work_item,
                allowed_tools=definition.allowed_tool_ids,
                allowed_skills=(skill_id,),
                arguments=tuple(
                    ArgumentValue.create(name, value)
                    for name, value in sorted(merged.items())
                ),
                requirement_ids=definition.requirement_ids,
                skill_hint=skill_id,
            )
            result = await executor(replace(context, work_item=derived))
            if self._control_guard is not None:
                self._control_guard.ensure_current(
                    context.work_item, context.trusted_context,
                )
            content = json.dumps({
                "status": result.status.value,
                "facts": {
                    fact.requirement_id: json.loads(fact.value_json)
                    for fact in result.facts
                },
                "response": result.candidate_response,
            }, ensure_ascii=False, sort_keys=True)
            return content, framework_artifact(result)

        return StructuredTool.from_function(
            coroutine=execute,
            name=skill_id,
            description=definition.objective,
            args_schema={
                "type": "object",
                "properties": properties,
                "required": list(definition.required_arguments),
                "additionalProperties": False,
            },
            infer_schema=False,
            response_format="content_and_artifact",
        )

    async def _prepare_prompt(self, context: AgentContextView, *, overhead_tokens: int) -> list[dict]:
        """Admit the pinned task; supply large source facts by archive reference.

        Working history is handled by ContextCompaction; this pinned task cannot
        be summarized away. Fact originals remain authoritative and unchanged.
        """
        values = [json.loads(fact.value_json) for fact in context.verified_facts]
        inline_budget = max(1, int((self._context_budget.available_tokens - overhead_tokens) * .25))
        for index, original in enumerate(context.verified_facts):
            if count_tokens_approximately([HumanMessage(original.value_json)]) > inline_budget:
                reference = await self._archive.save(context, {"content": original.value_json})
                values[index] = json.loads(result_pointer(reference, original.value_json))
        prompt = self._build_prompt(context, fact_values=values, overhead_tokens=overhead_tokens)
        from infrastructure.target_model_context import delegated_working_input
        _, task = delegated_working_input([], prompt, context.work_item.fingerprint)
        required = count_tokens_approximately([task]) + overhead_tokens
        if required > self._context_budget.available_tokens:
            raise ModelContextBudgetExceeded(required, self._context_budget.available_tokens)
        return prompt

    def _build_prompt(self, context: AgentContextView, *, fact_values=None, overhead_tokens=0) -> list[dict]:
        from application.business_observation import business_observation_context, assigned_business_observations
        item = context.work_item
        observations = business_observation_context(assigned_business_observations(
            context.trusted_context.get("business_observations", ()),
            work_item_ids=(item.work_item_id, item.continuation_of, *item.dependencies,
                           *(result.work_item_id for result in context.dependency_results)),
            source_refs=(*context.evidence_refs, *(fact.source_ref for fact in context.verified_facts))))
        if observations and 'read_conversation_observation' in item.allowed_tools:
            from application.historical_context_budget import fit_historical_payload
            observations = fit_historical_payload(self._context_budget, observations,
                observation_path=('business_observations',), overhead_tokens=overhead_tokens,
                inline_publication_ids=frozenset()).payload
        payload = {
            **observations,
            "objective": item.objective,
            "assignment_view": context.trusted_context.get("assignment_view", {}),
            "action_decisions": [decision for decision in context.trusted_context.get("action_decisions", ())
                                 if item.control and decision["control_id"] == item.control.control_id],
            "action_proposals_allowed": bool(item.allowed_actions) and context.pending_approval is None,
            "source_conversation": {"current_message": context.current_message},
            "arguments": {
                argument.name: argument.value for argument in item.arguments
            },
            "requirements": list(item.requirement_ids),
            "completed_actions": [
                receipt.__dict__ for result in context.dependency_results
                for receipt in result.action_receipts
            ],
            "verified_facts": [{
                "subject_ref": fact.subject_ref,
                "requirement_id": fact.requirement_id,
                "value": fact_values[index] if fact_values is not None else json.loads(fact.value_json),
                "source_ref": fact.source_ref,
                "producer_version": fact.producer_version,
                "source_kind": fact.source_kind.value,
                "producer_id": fact.producer_id,
                "observation_started_at": (fact.observation_started_at.isoformat()
                                           if fact.observation_started_at else None),
                "observed_at": fact.observed_at.isoformat(),
                "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
            } for index, fact in enumerate(context.verified_facts)],
            "recent_relevant_turns": list(context.recent_relevant_turns),
            "evidence_refs": list(context.evidence_refs),
            "pending_approval": ({
                "operations": [op.view() for op in context.pending_approval.operations],
                "status": "AWAITING_DECISION_NOT_EXECUTED",
            } if context.pending_approval else None),
        }
        # Whole background is admitted/archived by the working-context editor,
        # not silently trimmed before it can be summarized.
        return delegated_task_content(payload)

    def _system(self, context: AgentContextView) -> str:
        can_prepare = bool(context.work_item.allowed_actions) and context.pending_approval is None
        action_tools = tuple(tool for ref in context.work_item.allowed_actions
                             for tool in self._registry.action(ref).allowed_tool_ids) if can_prepare else ()
        references = {tool.name: tool.description for tool in self._tool_manager.tools_for_agent(
            self._registry.agent(context.work_item.owner_agent).execution_principal,
            allowed_tool_ids=action_tools)} if action_tools else {}
        return domain_instructions(
            self._system_prompt, owner=context.work_item.owner_agent,
            references=references, can_prepare=can_prepare,
            pending_approval=context.pending_approval is not None,
        )

    def _failure(
        self,
        context: AgentContextView,
        reason: str,
        *,
        retryable: bool = False,
        error: Exception | None = None,
        stage: str = "agent_execution",
        related_errors=(),
    ) -> AgentResult:
        from core.tracing import exception_chain
        item = context.work_item
        detail = {"code": reason, "retryable": retryable,
                  "work_item_id": item.work_item_id, "owner_agent": item.owner_agent,
                  "invocation_key": context.trusted_context.get("invocation_key"),
                  "conversation_id": context.trusted_context.get("conversation_id"),
                  "control_id": item.control.control_id if item.control else None,
                  "revision": item.control.revision if item.control else None,
                  "exception_chain": exception_chain(error) if error is not None else [],
                  "related_errors": list(related_errors)}
        diagnostic = {"stage": stage, "status": "failed", "detail": detail}
        logger.error("Domain execution failed: %s", json.dumps(diagnostic, ensure_ascii=False))
        if self._trace_sink:
            try:
                self._trace_sink.record_failure(diagnostic)
            except Exception:
                logger.exception("Failure trace export failed; preserving the domain diagnostic")
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            (
                AgentResultStatus.RETRYABLE_FAILURE
                if retryable else AgentResultStatus.TERMINAL_FAILURE
            ),
            reason,
            self.version,
            retryable=retryable,
            execution_feedback=(diagnostic,),
        )


def _adapt_framework_result(
    context: AgentContextView,
    observed: tuple[ToolResult | AgentResult, ...],
    producer_version: str,
    *,
    allowed_authorities: Mapping[str, str],
    candidate_response: str | None = None,
    accepted_outcome: Mapping | None = None,
) -> AgentResult:
    item = context.work_item
    tool_results = tuple(result for result in observed if isinstance(result, ToolResult))
    skill_results = tuple(result for result in observed if isinstance(result, AgentResult))
    pending = tuple(action for result in skill_results for action in result.prepared_actions)
    handback = (accepted_outcome or {}).get("kind")
    reassignment = next((result for result in skill_results
                         if result.assignment_issue and result.producer_version == "domain-interaction-v1"), None)
    facts = merge_facts(
        # The dispatcher already authorized these dependency/continuation
        # facts. Consuming evidence does not grant permission to produce it.
        context.verified_facts,
        tuple(
            fact_from_tool_result(item, result)
            for result in tool_results
            if result.success
            and result.observed_at is not None
            and result.authority == allowed_authorities.get(result.tool_name)
            and (not item.requirement_ids or result.authority in item.requirement_ids)
            and (tool_domain_outcome(result) is None or tool_domain_outcome(result)[0] is AgentResultStatus.SUCCEEDED)
        ),
        tuple(
            fact for result in skill_results for fact in result.facts
            if (not item.requirement_ids or fact.requirement_id in item.requirement_ids)
            and fact.requirement_id in allowed_authorities.values()
        ),
    )
    missing = set(item.requirement_ids).difference(
        fact.requirement_id for fact in facts
    )
    missing_inputs = tuple(
        field for result in skill_results for field in result.missing_inputs
        if result.producer_version == "domain-interaction-v1"
    )
    failed_tools = tuple(result for result in tool_results if not result.success)
    invalid_authority = any(
        result.success and result.authority != allowed_authorities.get(result.tool_name)
        for result in tool_results
    )
    domain_failures = tuple(
        outcome for result in tool_results
        if (outcome := tool_domain_outcome(result)) is not None
        and outcome[0] is not AgentResultStatus.SUCCEEDED
    )
    if pending:
        status = AgentResultStatus.WAITING_APPROVAL
        reason = "ACTION_PROPOSED"
        retryable = False
    elif invalid_authority:
        status = AgentResultStatus.TERMINAL_FAILURE
        reason = "FRAMEWORK_AGENT_INVALID_TOOL_AUTHORITY"
        retryable = False
    elif any(result.success and result.observed_at is None for result in tool_results):
        status = AgentResultStatus.TERMINAL_FAILURE
        reason = "TOOL_OBSERVATION_TIME_MISSING"
        retryable = False
    elif missing_inputs and handback == "NEEDS_USER_INPUT":
        status = AgentResultStatus.NEEDS_USER_INPUT
        reason = "FRAMEWORK_AGENT_NEEDS_USER_INPUT"
        retryable = False
    elif reassignment is not None and handback == "BLOCKED":
        status = AgentResultStatus.TERMINAL_FAILURE
        reason = reassignment.reason_code
        retryable = False
    elif any(result.status is AgentResultStatus.BLOCKED for result in skill_results) and handback == "BLOCKED":
        status = AgentResultStatus.BLOCKED
        reason = next(result.reason_code for result in skill_results if result.status is AgentResultStatus.BLOCKED)
        retryable = False
    elif (missing or not item.requirement_ids) and domain_failures:
        status, reason = next((outcome for outcome in domain_failures
                               if outcome[0] is AgentResultStatus.TERMINAL_FAILURE),
                              next((outcome for outcome in domain_failures
                                    if outcome[0] is AgentResultStatus.RETRYABLE_FAILURE), domain_failures[-1]))
        retryable = status is AgentResultStatus.RETRYABLE_FAILURE
    elif not item.requirement_ids and failed_tools and not facts:
        retryable = any(_retryable_tool_result(result) for result in failed_tools)
        status = (AgentResultStatus.RETRYABLE_FAILURE if retryable
                  else AgentResultStatus.TERMINAL_FAILURE)
        reason = "FRAMEWORK_AGENT_TOOL_FAILURE"
    elif not missing and candidate_response and candidate_response.strip() and (accepted_outcome or {}).get("kind") == "COMPLETE":
        status = AgentResultStatus.PARTIAL if failed_tools and not item.requirement_ids else AgentResultStatus.SUCCEEDED
        reason = "FRAMEWORK_AGENT_PARTIAL_RESULTS" if status is AgentResultStatus.PARTIAL else "FRAMEWORK_AGENT_REQUIREMENTS_SATISFIED"
        retryable = False
    elif any(_retryable_tool_result(result) for result in failed_tools):
        status = AgentResultStatus.RETRYABLE_FAILURE
        reason = "FRAMEWORK_AGENT_TOOL_FAILURE"
        retryable = True
    else:
        status = AgentResultStatus.TERMINAL_FAILURE
        reason = ("FRAMEWORK_AGENT_REQUIREMENTS_MISSING" if missing else
                  "DOMAIN_OUTCOME_NOT_ACCEPTED" if candidate_response or missing_inputs else "AGENT_RESPONSE_MISSING")
        retryable = False
    evidence_refs = tuple(dict.fromkeys((
        *(ref for result in skill_results for ref in result.evidence_refs),
        *(
            str(result.receipt_id or result.call_id)
            for result in tool_results
            if str(result.receipt_id or result.call_id).strip()
        ),
    )))
    return AgentResult(
        item.work_item_id,
        item.owner_agent,
        status,
        reason,
        producer_version,
        facts=facts,
        evidence_refs=evidence_refs,
        missing_inputs=missing_inputs if status is AgentResultStatus.NEEDS_USER_INPUT else (),
        candidate_response=(None if missing_inputs else candidate_response or next(
            (result.candidate_response for result in skill_results if result.candidate_response), None)),
        retryable=retryable,
        pending_action=pending[0] if pending else None,
        additional_actions=pending[1:],
        assignment_issue=(reassignment.assignment_issue if reassignment is not None
                          and reason == reassignment.reason_code else None),
    )


def _tool_output(result: ToolResult) -> str:
    if result.output_for_model:
        return result.output_for_model
    return json.dumps(
        result.data if result.success else {"error": result.error, "status": result.status},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _retryable_tool_result(result: ToolResult) -> bool:
    return result.status in {
        ToolCallStatus.ERROR.value,
        ToolCallStatus.TIMEOUT.value,
    }
