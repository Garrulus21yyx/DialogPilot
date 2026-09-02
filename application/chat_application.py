"""The protocol-neutral owner of the customer-service chat lifecycle.

M0 deliberately preserves the existing routing, retrieval, verification and
memory semantics.  This boundary makes their ordering callable without an HTTP
request and gives later admission/runtime milestones a stable outcome algebra.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import nullcontext
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Optional, TypeAlias

from memory.context import ContextSection
from core.identity import IdentityContractError, IdentityFactory, InvocationIdentity
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)


logger = logging.getLogger(__name__)


class StageStatus(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class StageObservation:
    stage: str
    status: StageStatus
    detail: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ChatCommand:
    """Authenticated application input; protocol adapters must resolve identity."""

    message: str
    user_id: str
    tenant_id: str = "default"
    conv_id: Optional[str] = None
    request_id: Optional[str] = None
    continuation_id: Optional[str] = None
    pinned_bundle: Any = None
    authorization_fingerprint: str = ""


@dataclass(frozen=True)
class RoutePathInvocation:
    """Immutable input passed to the gated route-specific execution adapter."""

    command: ChatCommand
    identity_metadata: Mapping[str, str]
    bundle: Any
    intent_result: Any
    orchestration_request: Any
    shape_decision: Any
    route_decision: Any
    requirements: tuple[Any, ...]
    execution_contract: Any


@dataclass(frozen=True)
class SkippedKnowledgeContext:
    text: str = ""
    used: bool = False
    citations: tuple[str, ...] = ()
    generation_status: str = "not_applicable"
    answer: str = ""
    claims: tuple[Mapping[str, Any], ...] = ()
    conflicts: tuple[Mapping[str, Any], ...] = ()
    abstained: bool = False
    reason: str = "route_forbids_pre_retrieval"
    evidence_pack: Any = None


@dataclass(frozen=True)
class Completed:
    response_id: str
    response: Mapping[str, Any]
    stages: tuple[StageObservation, ...] = ()


@dataclass(frozen=True)
class Accepted:
    workflow_run_id: str
    public_status: Mapping[str, Any]


@dataclass(frozen=True)
class NeedsInput:
    workflow_run_id: str
    signal_id: str
    kind: str
    expires_at: str
    interaction_publication_id: str


@dataclass(frozen=True)
class HandedOff:
    ticket_id: str
    handoff_id: str


@dataclass(frozen=True)
class Cancelled:
    workflow_run_id: str
    reason_code: str


@dataclass(frozen=True)
class Expired:
    workflow_run_id: str
    stage: str
    new_request_required: bool = True


@dataclass(frozen=True)
class Reconciling:
    workflow_run_id: str
    public_status: Mapping[str, Any]
    next_poll_after: float


@dataclass(frozen=True)
class Rejected:
    code: str
    safe_message: str


@dataclass(frozen=True)
class Conflict:
    code: str
    existing_invocation: Mapping[str, Any]


@dataclass(frozen=True)
class Failed:
    code: str
    retryable: bool
    correlation_id: str
    safe_message: str = ""
    stages: tuple[StageObservation, ...] = ()


ChatOutcome: TypeAlias = (
    Completed
    | Accepted
    | NeedsInput
    | HandedOff
    | Cancelled
    | Expired
    | Reconciling
    | Rejected
    | Conflict
    | Failed
)


@dataclass(frozen=True)
class ChatServices:
    orchestrator: Any
    memory: Any
    answer_verifier: Any
    ticket_service: Any
    response_delivery: Any
    context_assembler: Any
    bundle_registry: Any
    rollout_manager: Any
    tool_manager: Any = None
    trace_recorder: Any = None
    knowledge_base: Any = None
    route_execution_mode: str = "legacy"

    @property
    def ready(self) -> bool:
        return all(
            component is not None
            for component in (
                self.orchestrator,
                self.memory,
                self.answer_verifier,
                self.ticket_service,
                self.response_delivery,
                self.context_assembler,
                self.bundle_registry,
                self.rollout_manager,
            )
        )


@dataclass(frozen=True)
class ChatOperations:
    active_ticket_context: Callable[[str], Awaitable[Any]]
    build_knowledge_context: Callable[..., Awaitable[Any]]
    capture_badcases: Callable[..., Awaitable[None]]
    evaluate_shadow: Callable[..., Awaitable[None]]
    handoff_priority: Callable[[Any, str], Any]
    policy_terminal_verification: Callable[[Any], Optional[VerificationResult]]
    publish_candidate: Callable[[str, VerificationResult], str]
    public_agent_outcomes: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    record_intent_prediction: Callable[..., Awaitable[Any]]
    select_publication_candidate: Callable[..., tuple[str, bool]]
    trace_id: Callable[[], str]
    verify_for_publication: Callable[..., Awaitable[VerificationResult]]
    evaluate_route_path: Optional[
        Callable[[RoutePathInvocation], Awaitable[Any]]
    ] = None


class ChatApplication:
    """Owns the complete current chat invocation from context load to publication."""

    def __init__(
        self,
        services: ChatServices,
        operations: ChatOperations,
        identity_factory: IdentityFactory | None = None,
    ):
        self._services = services
        self._ops = operations
        self._identity_factory = identity_factory or IdentityFactory()

    async def handle(self, command: ChatCommand) -> ChatOutcome:
        if not self._services.ready:
            return Failed(
                code="service_unavailable",
                retryable=True,
                correlation_id=self._ops.trace_id(),
                safe_message="服务未就绪",
            )
        try:
            identity = self._identity_factory.create_invocation(
                tenant_id=command.tenant_id,
                user_id=command.user_id,
                conversation_id=command.conv_id,
                request_id=command.request_id,
                continuation_id=command.continuation_id,
            )
            span = (
                self._services.trace_recorder.span(
                    "application.chat",
                    kind="internal",
                    attributes=identity.metadata(),
                )
                if self._services.trace_recorder is not None
                else nullcontext()
            )
            with span:
                return await self._handle_ready(command, identity)
        except IdentityContractError:
            return Rejected(
                code="invalid_invocation_identity",
                safe_message="请求身份字段无效",
            )
        except Exception as exc:
            logger.exception(
                "chat application failed request_id=%s", command.request_id or "unassigned",
            )
            return Failed(
                code="chat_application_failed",
                retryable=False,
                correlation_id=self._ops.trace_id(),
                safe_message=f"internal application failure: {type(exc).__name__}",
            )

    async def execute_pinned(
        self,
        command: ChatCommand,
        identity: InvocationIdentity,
        *,
        assignment: Any,
        publication_guard: Callable[[], Awaitable[None]],
    ) -> ChatOutcome:
        """Execute one already-admitted invocation against its immutable pins."""
        if not self._services.ready:
            return Failed(
                code="service_unavailable",
                retryable=True,
                correlation_id=self._ops.trace_id(),
                safe_message="服务未就绪",
            )
        try:
            span = (
                self._services.trace_recorder.span(
                    "application.chat.durable_execution",
                    kind="internal",
                    attributes=identity.metadata(),
                )
                if self._services.trace_recorder is not None
                else nullcontext()
            )
            with span:
                return await self._handle_ready(
                    command,
                    identity,
                    assignment=assignment,
                    publication_guard=publication_guard,
                )
        except Exception as exc:
            logger.exception(
                "durable chat execution failed invocation_key=%s",
                identity.invocation_key,
            )
            return Failed(
                code="chat_application_failed",
                retryable=False,
                correlation_id=self._ops.trace_id(),
                safe_message=f"internal application failure: {type(exc).__name__}",
            )

    async def _handle_ready(
        self,
        command: ChatCommand,
        identity: InvocationIdentity,
        *,
        assignment: Any = None,
        publication_guard: Callable[[], Awaitable[None]] | None = None,
    ) -> ChatOutcome:
        from agents.agent_orchestrator import Request as OrcReq
        from memory.conversation_memory import MsgRole

        services = self._services
        ops = self._ops
        user_id = str(identity.user_id)
        conv_id = str(identity.conversation_id)
        request_id = str(identity.request_id)
        identity_metadata = {
            **identity.metadata(),
            "authorization_fingerprint": command.authorization_fingerprint,
        }
        stages: list[StageObservation] = []
        assignment = assignment or await asyncio.to_thread(
            services.rollout_manager.resolve, user_id,
        )
        if not getattr(assignment, "admission_allowed", True):
            return Failed(
                code="rollout_admission_blocked",
                retryable=False,
                correlation_id=ops.trace_id(),
                safe_message=(
                    "当前服务版本无法安全回退，已停止自动处理并等待人工处置。"
                ),
                stages=(StageObservation("rollout_admission", StageStatus.FAILED, {
                    "reason_code": getattr(
                        assignment, "admission_reason", "ROLLOUT_BLOCKED",
                    ),
                }),),
            )
        bundle = command.pinned_bundle or assignment.primary
        if command.pinned_bundle is not None:
            assignment = type("EvalAssignment", (), {
                "primary": bundle,
                "primary_stage": "evaluation",
                "shadow": None,
                "pinned_refs": None,
                "shadow_pinned_refs": None,
                "admission_allowed": True,
            })()
        pinned_refs = getattr(assignment, "pinned_refs", None)
        stages.append(StageObservation("rollout_admission", StageStatus.OK, {
            "bundle_version": bundle.version,
            "assignment_stage": assignment.primary_stage,
            "publisher_version": bundle.version,
            "pinned_refs_fingerprint": (
                pinned_refs.fingerprint if pinned_refs is not None else "evaluation"
            ),
        }))

        mem_ctx = await services.memory.get_context(user_id, conv_id, query=command.message)
        stages.append(StageObservation("memory_load", StageStatus.OK, {
            "recent_message_count": len(mem_ctx.recent_messages),
            "retrieval_hit_count": len(mem_ctx.retrieval_hits),
        }))
        prompt_history = [
            {"role": message.role.value, "content": message.content}
            for message in mem_ctx.recent_messages
        ]
        intent_history = [
            {"role": message.role.value, "content": message.content}
            for message in mem_ctx.recent_messages[-5:]
        ] if mem_ctx.recent_messages else None

        intent_result = await services.orchestrator.recognize_intent(
            command.message, history=intent_history, bundle=bundle,
        )
        stages.append(StageObservation("intent", StageStatus.OK, {
            "intent": intent_result.intent.value,
            "confidence": intent_result.confidence,
        }))
        intent_prediction = await ops.record_intent_prediction(
            intent_result=intent_result,
            request_id=request_id,
            conv_id=conv_id,
            user_id=user_id,
            message=command.message,
            bundle=bundle,
        )
        route_path = await self._dispatch_route_path_if_enabled(
            command=command,
            identity_metadata=identity_metadata,
            bundle=bundle,
            intent_result=intent_result,
            user_id=user_id,
            conv_id=conv_id,
            request_id=request_id,
            pinned_execution_refs=getattr(assignment, "pinned_refs", None),
            stages=stages,
        )
        route_mode = route_path.route_decision.mode.value if route_path else "legacy"
        if route_mode in {"knowledge_qa", "mixed", "legacy"}:
            knowledge = await ops.build_knowledge_context(
                command.message,
                intent=intent_result.intent,
                bundle=bundle,
                history=[str(item.get("content") or "") for item in prompt_history],
                tenant_id=str(identity.tenant_id),
                user_id=user_id,
                conversation_id=conv_id,
                authorization_fingerprint=command.authorization_fingerprint,
                generate_answer=route_mode != "mixed",
                pinned_execution_refs=getattr(assignment, "pinned_refs", None),
            )
        else:
            knowledge = SkippedKnowledgeContext()
        stages.append(StageObservation("knowledge_retrieval", StageStatus.OK, {
            "used": bool(knowledge.used),
            "generation_status": knowledge.generation_status,
            "citation_count": len(knowledge.citations),
        }))
        base_context_sections = list(mem_ctx.to_sections())
        active_ticket_section = await ops.active_ticket_context(user_id)
        if active_ticket_section is not None:
            base_context_sections.append(active_ticket_section)
        context_sections = list(base_context_sections)
        if knowledge.text:
            context_sections.append(ContextSection(
                tag="knowledge",
                description="检索到的公共业务知识及有引用草稿；实时账户事实仍以业务工具为准",
                content=knowledge.text,
                priority=85,
            ))
        prompt_context = services.context_assembler.assemble(
            sections=context_sections,
            history=prompt_history,
            current_user_message=command.message,
        )
        full_context = prompt_context.system_context
        orchestration_request = OrcReq(
            message=command.message,
            user_id=user_id,
            conv_id=conv_id,
            context=full_context,
            history=intent_history,
            prompt_context=prompt_context,
            entities=intent_result.entities,
            intent=intent_result.intent,
            intent_group=intent_result.intent_group,
            urgency=intent_result.urgency,
            intent_confidence=intent_result.confidence,
            request_id=request_id,
            bundle_version=bundle.version,
            agent_bundle=bundle,
            pinned_execution_refs=(
                getattr(assignment, "pinned_refs", None).__dict__
                if getattr(assignment, "pinned_refs", None) is not None else {}
            ),
            identity_metadata=identity_metadata,
            intent_classifier_fingerprint=str(
                getattr(intent_result, "classifier_fingerprint", "") or ""
            ),
            intent_input_fingerprint=str(
                getattr(intent_result, "input_fingerprint", "") or ""
            ),
            intent_source_scores=dict(intent_result.source_scores),
            domain_decision=(
                route_path.orchestration_request.domain_decision
                if route_path is not None else None
            ),
            routing_policy_trace=(
                route_path.orchestration_request.routing_policy_trace
                if route_path is not None else None
            ),
        )
        result = await services.orchestrator.run(orchestration_request)
        stages.append(StageObservation("route_and_agent", StageStatus.OK, {
            "routing_disposition": getattr(
                result.routing_disposition, "value", result.routing_disposition,
            ),
            "agent_count": len(result.agent_types),
            "awaiting_approval": bool(result.awaiting_approval),
        }))

        approval_pending = bool(result.awaiting_approval)
        publication_candidate, knowledge_is_final = ops.select_publication_candidate(
            result.response,
            knowledge,
            approval_pending=approval_pending,
            route_mode=route_mode,
        )
        knowledge_verification = {
            "mode": "grounded_final" if knowledge_is_final else "mixed_or_context_only",
            "grounded_answer": knowledge.answer,
            "claims": list(knowledge.claims),
            "conflicts": list(knowledge.conflicts),
            "citations": list(knowledge.citations),
            "abstained": knowledge.abstained,
            "reason": knowledge.reason,
            "evidence_pack": (
                knowledge.evidence_pack.to_dict(include_text=True)
                if knowledge.evidence_pack is not None else {}
            ),
        }
        if approval_pending:
            verification = VerificationResult(
                status=VerificationStatus.UNKNOWN,
                grounded=False,
                need_escalation=False,
                reason="high-risk tool call is paused for authenticated host approval",
                reason_code=VerificationReasonCode.APPROVAL_REQUIRED,
            )
        else:
            verification = ops.policy_terminal_verification(result)
            if verification is None:
                verification = await ops.verify_for_publication(
                    services.answer_verifier,
                    command.message,
                    publication_candidate,
                    full_context,
                    task_plan=result.task_plan,
                    coverage=result.coverage,
                    agent_outcomes=result.agent_outcomes,
                    knowledge_evidence=knowledge_verification,
                )
        stages.append(StageObservation("verification", StageStatus.OK, {
            "status": verification.status.value,
            "reason_code": verification.reason_code.value,
            "publishable": verification.publishable,
        }))
        feedback_recorder = getattr(services.orchestrator, "record_verification", None)
        if feedback_recorder:
            try:
                feedback_recorder(result.producer_agent_keys, verification.status.value)
            except Exception:
                logger.exception("记录 Agent 质量反馈失败 request_id=%s", request_id)
        response_text = result.response if approval_pending else ops.publish_candidate(
            publication_candidate, verification,
        )
        escalated = result.escalated or verification.need_escalation
        disposition = getattr(
            result.routing_disposition, "value", result.routing_disposition,
        )

        ticket = None
        handoff_created = False
        if escalated:
            if publication_guard is not None:
                await publication_guard()
            ticket_operation_key = identity.operation_key(
                "TicketService", "create_handoff", "primary",
            )
            try:
                ticket, handoff_created = await asyncio.to_thread(
                    services.ticket_service.create_ticket,
                    idempotency_key=str(ticket_operation_key),
                    user_id=user_id,
                    conv_id=conv_id,
                    request_id=request_id,
                    question=command.message,
                    published_response=response_text,
                    reason=(
                        f"verification={verification.status.value}: {verification.reason}; "
                        f"coverage={result.coverage.get('complete', 'unknown')}; "
                        f"routing={result.routing_reason}"
                    )[:5000],
                    priority=ops.handoff_priority(
                        intent_result.urgency, verification.status.value,
                    ),
                    agent_type=(
                        result.agent_type.value if result.agent_type else "orchestrator"
                    ),
                    intent=result.intent.value if result.intent else "other",
                    verification_status=verification.status.value,
                    identity_metadata={
                        **identity_metadata,
                        "operation_key": str(ticket_operation_key),
                    },
                )
                stages.append(StageObservation("ticket", StageStatus.OK, {
                    "ticket_id": ticket.ticket_id,
                    "created": handoff_created,
                }))
            except Exception:
                logger.exception("人工工单创建失败 request_id=%s", request_id)
                response_text = (
                    "当前回答需要人工确认，但工单创建失败。请稍后使用相同 request_id 重试，"
                    "或直接联系人工客服。"
                )
                stages.append(StageObservation("ticket", StageStatus.DEGRADED, {
                    "created": False,
                    "reason": "ticket_creation_failed",
                }))
        else:
            stages.append(StageObservation("ticket", StageStatus.SKIPPED, {
                "reason": "handoff_not_required",
            }))

        tool_audit = [
            record.to_dict()
            for record in services.tool_manager.audit_records(trace_id=ops.trace_id())
        ] if services.tool_manager else []
        response = {
            "request_id": request_id,
            "trace_id": ops.trace_id(),
            "conv_id": conv_id,
            "response_id": "",
            "response_seq": 0,
            "delivery_status": "SELECTED",
            "response": response_text,
            "intent": result.intent.value if result.intent else "other",
            "intent_group": intent_result.intent_group,
            "agent_type": result.agent_type.value if result.agent_type else "orchestrator",
            "agent_types": [item.value for item in result.agent_types],
            "primary_agent": result.primary_agent.value if result.primary_agent else "",
            "supporting_agents": [item.value for item in result.supporting_agents],
            "routing_reason": result.routing_reason,
            "routing_confidence": result.routing_confidence,
            "routing_disposition": disposition,
            "synthesis_status": result.synthesis_status,
            "synthesis_reason": result.synthesis_reason,
            "synthesis_conflicts": result.synthesis_conflicts,
            "agent_outcomes": ops.public_agent_outcomes(result.agent_outcomes),
            "task_plan": result.task_plan,
            "coverage": result.coverage,
            "execution_budget": result.execution_budget,
            "tool_audit": tool_audit,
            "memory_retrieval": [] if disposition == "out_of_scope" else [{
                "memory_id": hit.memory_id,
                "score": round(hit.score, 8),
                "sources": list(hit.sources),
                "ranks": dict(hit.ranks),
            } for hit in mem_ctx.retrieval_hits],
            "escalated": escalated,
            "latency_ms": round(result.latency_ms, 1),
            "knowledge_used": knowledge.used,
            "knowledge_draft_citations": list(knowledge.citations),
            "knowledge_citations": (
                list(knowledge.citations)
                if knowledge_is_final and verification.publishable else []
            ),
            "knowledge_claims": list(knowledge.claims),
            "knowledge_conflicts": list(knowledge.conflicts),
            "knowledge_evidence": (
                [item.to_dict(include_text=False) for item in knowledge.evidence_pack.items]
                if knowledge.evidence_pack is not None else []
            ),
            "knowledge_generation_status": (
                "grounded_final"
                if knowledge_is_final and verification.publishable
                else knowledge.generation_status
            ),
            "entities": intent_result.entities,
            "intent_confidence": round(intent_result.confidence, 4),
            "intent_source_scores": intent_result.source_scores,
            "intent_prediction_id": (
                intent_prediction.prediction_id if intent_prediction is not None else ""
            ),
            "intent_classifier_fingerprint": str(
                getattr(intent_result, "classifier_fingerprint", "") or ""
            ),
            "verification_status": verification.status.value,
            "verified": verification.publishable,
            "grounded": verification.grounded,
            "verification_reason": verification.reason,
            "verification_reason_code": verification.reason_code.value,
            "ticket_id": ticket.ticket_id if ticket else None,
            "ticket_status": ticket.status.value if ticket else None,
            "handoff_created": handoff_created,
            "bundle_version": bundle.version,
            "routing_policy_trace": result.routing_policy_trace,
            "rollout_stage": assignment.primary_stage,
            "awaiting_approval": approval_pending,
            "react_run_ids": list(result.react_run_ids),
            "pending_approval_call_ids": list(result.pending_approval_call_ids),
            "pending_signals": list(result.pending_signals),
        }

        try:
            if publication_guard is not None:
                await publication_guard()
            delivery_operation_key = identity.operation_key(
                "ResponseDelivery", "select_final_response", "primary",
            )
            delivery = await asyncio.to_thread(
                services.response_delivery.select_response,
                user_id=user_id,
                conv_id=conv_id,
                request_id=request_id,
                response_text=response_text,
                identity_metadata={
                    **identity_metadata,
                    "operation_key": str(delivery_operation_key),
                    "candidate_id": _publication_candidate_id(
                        str(identity.invocation_key), response_text,
                    ),
                    "producer": "chat-application-compat-v1",
                    "verifier_status": verification.status.value,
                    "verification": {
                        "status": verification.status.value,
                        "grounded": verification.grounded,
                        "need_escalation": verification.need_escalation,
                        "reason": verification.reason,
                        "reason_code": verification.reason_code.value,
                    },
                    "evidence_sha256": _canonical_sha256({
                        "verification": {
                            "status": verification.status.value,
                            "grounded": verification.grounded,
                            "need_escalation": verification.need_escalation,
                            "reason": verification.reason,
                            "reason_code": verification.reason_code.value,
                        },
                        "knowledge": knowledge_verification,
                        "coverage": result.coverage,
                    }),
                    "bundle_version": bundle.version,
                    "index_manifest_sha256": _knowledge_manifest_fingerprint(
                        knowledge, services.knowledge_base,
                    ),
                    "projection_disposition": (
                        "approval" if approval_pending else disposition
                    ),
                    "public_response": response,
                    "execution_stages": [item.to_dict() for item in stages],
                },
            )
        except Exception:
            logger.exception("持久化回答选择事实失败 request_id=%s", request_id)
            return Failed(
                code="response_selection_unavailable",
                retryable=True,
                correlation_id=ops.trace_id(),
                stages=tuple(stages) + (StageObservation(
                    "delivery", StageStatus.FAILED,
                    {"reason": "response_selection_unavailable"},
                ),),
            )
        stages.append(StageObservation("delivery", StageStatus.OK, {
            "response_id": delivery.response_id,
            "response_seq": delivery.seq,
            "status": delivery.status.value,
        }))
        response.update({
            "response_id": delivery.response_id,
            "response_seq": delivery.seq,
            "delivery_status": delivery.status,
        })
        stages.append(StageObservation("tool", StageStatus.OK, {
            "call_count": len(tool_audit),
            "statuses": [str(item.get("status") or "") for item in tool_audit],
        }))
        await ops.capture_badcases(
            req=command,
            user_id=user_id,
            request_id=request_id,
            result=result,
            verification=verification,
            published_response=response_text,
            tool_audit=tool_audit,
            bundle=bundle,
            approval_pending=approval_pending,
        )
        await asyncio.to_thread(
            services.rollout_manager.record_outcome,
            bundle_version=bundle.version,
            stage=assignment.primary_stage,
            verified=(
                verification.publishable
                and (
                    disposition in {"clarify", "out_of_scope"}
                    or bool(result.coverage.get("complete", False))
                )
                and not approval_pending
            ),
            latency_ms=result.latency_ms,
            cost_units=float(len(result.agent_outcomes) + len(tool_audit)),
            request_id=request_id,
        )
        if disposition != "out_of_scope":
            await services.memory.add_messages(user_id, conv_id, [
                (MsgRole.USER, command.message, identity_metadata),
                (MsgRole.ASSISTANT, response_text, {
                    **identity_metadata,
                    "response_id": delivery.response_id,
                    "response_seq": delivery.seq,
                }),
            ], extract_facts=(disposition == "execute"))
            stages.append(StageObservation("memory_write", StageStatus.OK, {
                "message_count": 2,
                "extract_facts": disposition == "execute",
            }))
        else:
            stages.append(StageObservation("memory_write", StageStatus.SKIPPED, {
                "reason": "out_of_scope_projection_policy",
            }))

        if assignment.shadow is not None and disposition == "execute":
            asyncio.create_task(ops.evaluate_shadow(
                request=command,
                user_id=user_id,
                conv_id=conv_id,
                request_id=request_id,
                bundle=assignment.shadow,
                base_sections=base_context_sections,
                prompt_history=prompt_history,
                intent_history=intent_history,
                identity_metadata=identity_metadata,
                pinned_execution_refs=getattr(
                    assignment, "shadow_pinned_refs", None,
                ),
            ))

        return Completed(
            response_id=delivery.response_id,
            response=response,
            stages=tuple(stages),
        )

    async def _dispatch_route_path_if_enabled(
        self,
        *,
        command: ChatCommand,
        identity_metadata: Mapping[str, str],
        bundle: Any,
        intent_result: Any,
        user_id: str,
        conv_id: str,
        request_id: str,
        stages: list[StageObservation],
        pinned_execution_refs: Any = None,
    ) -> Optional[RoutePathInvocation]:
        """Build one canonical route contract and dispatch only to a gated shadow."""
        mode = str(self._services.route_execution_mode or "legacy").strip().lower()
        if mode not in {"legacy", "dark_shadow", "evaluation"}:
            raise RuntimeError(
                "route execution cannot publish before the release action"
            )
        callback = self._ops.evaluate_route_path
        if mode != "legacy" and callback is None:
            raise RuntimeError("route execution adapter is unavailable")
        if not all(hasattr(self._services.orchestrator, name) for name in (
            "classify_request_shape", "decide_route",
        )):
            if mode == "legacy":
                return None
            raise RuntimeError("canonical route producer is unavailable")

        from agents.agent_orchestrator import Request as OrcReq
        from application.authority_policy import AuthorityPolicyRegistry
        from application.coverage_gate import VerificationProfileRegistry
        from application.route_execution import RouteExecutionPolicy

        request = OrcReq(
            message=command.message,
            user_id=user_id,
            conv_id=conv_id,
            entities=intent_result.entities,
            intent=intent_result.intent,
            intent_group=intent_result.intent_group,
            urgency=intent_result.urgency,
            intent_confidence=intent_result.confidence,
            request_id=request_id,
            bundle_version=bundle.version,
            agent_bundle=bundle,
            pinned_execution_refs=(
                pinned_execution_refs.__dict__
                if pinned_execution_refs is not None else {}
            ),
            execution_mode="shadow",
            identity_metadata=dict(identity_metadata),
            intent_classifier_fingerprint=str(
                getattr(intent_result, "classifier_fingerprint", "") or ""
            ),
            intent_input_fingerprint=str(
                getattr(intent_result, "input_fingerprint", "") or ""
            ),
            intent_source_scores=dict(intent_result.source_scores),
        )
        shape = await self._services.orchestrator.classify_request_shape(request)
        route = await self._services.orchestrator.decide_route(request, shape)
        policies = AuthorityPolicyRegistry.v1()
        requirements = policies.minimum_requirements(route)
        verification = VerificationProfileRegistry().contract_for(
            route.mode, requirements,
        )
        contract = RouteExecutionPolicy().plan(route, verification)
        invocation = RoutePathInvocation(
            command=command,
            identity_metadata=dict(identity_metadata),
            bundle=bundle,
            intent_result=intent_result,
            orchestration_request=request,
            shape_decision=shape,
            route_decision=route,
            requirements=requirements,
            execution_contract=contract,
        )
        stages.append(StageObservation("route_path_plan", StageStatus.OK, {
            "mode": route.mode.value,
            "shape": shape.shape.value,
            "contract_fingerprint": contract.fingerprint,
            "execution_mode": mode,
        }))
        if mode == "evaluation":
            await callback(invocation)
        elif mode == "dark_shadow":
            asyncio.create_task(callback(invocation))
        return invocation


def _publication_candidate_id(invocation_key: str, response_text: str) -> str:
    return f"candidate:v1:{_canonical_sha256({
        'invocation_key': invocation_key,
        'response_text': response_text,
    })}"


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _knowledge_manifest_fingerprint(knowledge: Any, knowledge_base: Any) -> str:
    evidence_pack = getattr(knowledge, "evidence_pack", None)
    fingerprint = str(
        getattr(evidence_pack, "index_manifest_fingerprint", "") or ""
    )
    if not fingerprint and knowledge_base is not None:
        manifest = getattr(knowledge_base, "index_manifest", {})
        if callable(manifest):
            manifest = manifest()
        fingerprint = str(dict(manifest or {}).get("manifest_fingerprint") or "")
    return fingerprint if len(fingerprint) == 64 else ""
