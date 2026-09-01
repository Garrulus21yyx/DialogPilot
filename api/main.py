"""
DialogPilot 智能客服系统 — FastAPI 入口

所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import hashlib
import json
import logging
import os
import pathlib
import re
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Response, UploadFile, File, Query, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from services.ticket_service import (
    IdempotencyConflictError,
    InvalidTransitionError,
    TicketNotFoundError,
    TicketPriority,
    TicketService,
    TicketStatus,
    TicketWebhookDispatcher,
)
from services.response_delivery import (
    DeliveryStatus,
    ResponseDeliveryService,
    ResponseNotFoundError,
)
from services.badcase_registry import (
    BadCaseContractError,
    BadCaseNotFoundError,
    BadCaseRegistry,
    BadCaseSeverity,
    BadCaseStage,
    BadCaseStatus,
    BadCaseTransitionError,
    IntentFeedbackStatus,
)
from memory.context import ContextAssembler, ContextSection
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.grounded_answer_generator import GroundedAnswerGenerator
from mcp.evidence_pack import EvidencePack
from mcp.source_document import SourceDocument, SourceDocumentContractError
from core.tracing import TraceRecorder, current_trace_id, trace_scope
from core.input_security import PromptInjectionGuard
from core.auth import AuthenticationError, AuthorizationError, JWTAuthenticator, Principal
from core.llm_metrics import capture_llm_usage
from core.model_policy import ModelPolicy, ModelRole
from core.rag_policy import DEFAULT_RAG_RETRIEVAL_POLICY, rag_retrieval_policy_from_env
from core.intent_recognizer import IntentCategory
from core.identity import IdentityFactory
from application.chat_application import (
    ChatApplication,
    ChatCommand,
    ChatOperations,
    ChatServices,
    Completed,
    Failed,
    Rejected,
)
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.evolution import (
    AgentBundle,
    AgentBundleRegistry,
    BundleConflictError,
    BundleContractError,
    BundleNotFoundError,
    EvolutionEnvelope,
    BadCaseMiner,
    CreditAttributor,
    RolloutContractError,
    RolloutManager,
    SoftRollbackPolicy,
    build_default_bundle,
    build_llm_proposal_generator,
)

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
   ╔════════════════════════╗
   ║   DialogPilot  v2.0       ║
   ║   Multi-Agent Support     ║
   ╚════════════════════════╝
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_knowledge_base = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_skill_manager = None
_answer_verifier = None
_ticket_service = None
_response_delivery = None
_badcase_registry = None
_customer_operations = None
_context_assembler = None
_rag_context_packer = ContextPacker()
_grounded_answer_generator = None
_authenticator = None
_model_policy = None
_run_store = None
_bundle_registry = None
_proposal_generator = None
_rollout_manager = None
_trace_recorder = TraceRecorder()
_input_security_guard = PromptInjectionGuard()


def get_principal(authorization: str = Header(default="")) -> Principal:
    """在 HTTP 边界把签名 Token 转成 Principal；业务层不再信任 user_id 字符。"""
    if _authenticator is None:
        raise HTTPException(503, "认证服务未就绪")
    try:
        return _authenticator.authenticate(authorization)
    except AuthenticationError as exc:
        raise HTTPException(401, "无效或缺失的 Bearer Token") from exc


def require_scopes(*required: str):
    """生成稳定的 FastAPI scope 依赖，admin 可显式越过细分 scope。"""
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        try:
            principal.require(required)
        except AuthorizationError as exc:
            raise HTTPException(403, "当前身份无权访问该资源") from exc
        return principal
    return dependency


_chat_principal = require_scopes("chat")
_admin_principal = require_scopes("admin")
_knowledge_principal = require_scopes("knowledge:read")
_tool_approval_principal = require_scopes("tool:approve")

def _anthropic_cfg() -> Dict[str, Any]:
    """读取模型供应商配置，并在应用启动前验证必需 API Key。"""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")
    policy = ModelPolicy.from_env()
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    policy.profile(ModelRole.WORKER).model,
        "policy": policy,
    }
    base_url = policy.base_url
    if base_url:
        cfg["base_url"] = base_url
    return cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    """按依赖顺序创建所有组件，并在退出时释放后台任务和连接。"""
    global _orchestrator, _memory, _knowledge_base, _tool_manager, _monitor, _evaluator, _skill_manager, _answer_verifier, _ticket_service, _response_delivery, _badcase_registry, _customer_operations, _context_assembler, _authenticator, _model_policy, _run_store, _bundle_registry, _proposal_generator, _rollout_manager, _grounded_answer_generator

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator
    from agents.run_store import RunStore
    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from mcp.knowledge_base import KnowledgeBase
    from mcp.customer_support_tools import ticket_tools
    from mcp.customer_operations_tools import customer_operation_tools
    from mcp.tool_manager import ApprovalMode, MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from monitor.performance_monitor import PerformanceMonitor
    from core.skill_loader import SkillManager
    from services.answer_verifier import AnswerVerifier
    from services.customer_operations import CustomerOperationsService

    cfg = _anthropic_cfg()
    _model_policy = cfg["policy"]
    similarity_mode = os.getenv("INTENT_SIMILARITY_MODE", "ngram")
    chroma_mode = os.getenv("CHROMA_MODE", "remote")
    _authenticator = JWTAuthenticator.from_env()
    _run_store = RunStore(
        os.getenv(
            "REACT_RUN_DB_PATH",
            str(pathlib.Path(_ROOT) / "data" / "react-runs" / "react-runs.db"),
        ),
        approval_ttl_s=float(os.getenv("REACT_APPROVAL_TTL_SECONDS", "900")),
        recovery_grace_s=float(os.getenv("REACT_RECOVERY_GRACE_SECONDS", "30")),
    )
    _bundle_registry = AgentBundleRegistry(
        os.getenv(
            "AGENT_BUNDLE_DB_PATH",
            str(pathlib.Path(_ROOT) / "data" / "evolution" / "agent-bundles.db"),
        )
    )
    bootstrap_rag_policy = rag_retrieval_policy_from_env(os.environ)
    default_bundle = build_default_bundle(_model_policy.to_dict(), bootstrap_rag_policy)
    _, bundle_migrated = _bundle_registry.bootstrap_successor(
        default_bundle,
        predecessor_version="agent-v1",
        expected_predecessor_retrieval={
            "top_k": 3,
            "rrf_k": 60,
            "vector_weight": 0.0,
            "lexical_weight": 1.0,
        },
    )
    if bundle_migrated:
        logger.info("Active bootstrap Bundle migrated to %s", default_bundle.version)
    _proposal_generator = build_llm_proposal_generator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model_profile=_model_policy.profile(ModelRole.JUDGE),
    )
    logger.info("模型分层策略: %s", _model_policy.to_dict())

    # 单一意图识别器同时注入 Orchestrator 与 Evaluator，避免 cache/学习状态分叉。
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        similarity_mode=similarity_mode,
        model_profile=_model_policy.profile(ModelRole.INTENT),
        cache_ttl_seconds=float(os.getenv("INTENT_CACHE_TTL_SECONDS", "3600")),
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("DIALOGPILOT_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("DIALOGPILOT_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()

    # Agent 编排器
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=_skill_manager,
        agent_timeout_s=float(os.getenv("AGENT_TIMEOUT_SECONDS", "15")),
        request_timeout_s=float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "20")),
        max_agents_per_request=int(os.getenv("AGENT_MAX_PER_REQUEST", "3")),
        react_max_steps=int(os.getenv("REACT_MAX_STEPS", "4")),
        intent_similarity_mode=similarity_mode,
        model_policy=_model_policy,
        run_store=_run_store,
        intent_recognizer=recognizer,
    )
    _answer_verifier = AnswerVerifier(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        model_profile=_model_policy.profile(ModelRole.VERIFIER),
    )
    ticket_webhook_url = os.getenv("TICKET_DISPATCH_WEBHOOK_URL", "").strip()
    ticket_dispatcher = TicketWebhookDispatcher(
        ticket_webhook_url,
        timeout_seconds=float(os.getenv("TICKET_DISPATCH_TIMEOUT_SECONDS", "5")),
    ) if ticket_webhook_url else None
    _ticket_service = TicketService(
        os.getenv(
            "TICKET_DB_PATH",
            str(pathlib.Path(_ROOT) / "data" / "tickets" / "tickets.db"),
        ),
        dispatcher=ticket_dispatcher,
        dispatch_poll_seconds=float(os.getenv("TICKET_DISPATCH_POLL_SECONDS", "5")),
        dispatch_lease_seconds=float(os.getenv("TICKET_DISPATCH_LEASE_SECONDS", "30")),
        dispatch_retry_base_seconds=float(os.getenv("TICKET_DISPATCH_RETRY_BASE_SECONDS", "5")),
        dispatch_retry_max_seconds=float(os.getenv("TICKET_DISPATCH_RETRY_MAX_SECONDS", "300")),
    )
    _response_delivery = ResponseDeliveryService(
        os.getenv(
            "RESPONSE_DELIVERY_DB_PATH",
            str(pathlib.Path(_ROOT) / "data" / "responses" / "responses.db"),
        )
    )
    _badcase_registry = BadCaseRegistry(
        os.getenv(
            "BADCASE_DB_PATH",
            str(pathlib.Path(_ROOT) / "data" / "badcases" / "badcases.db"),
        ),
        identity_salt=os.getenv("BADCASE_IDENTITY_SALT") or os.getenv("AUTH_JWT_SECRET", ""),
    )
    _customer_operations = CustomerOperationsService(
        os.getenv(
            "CUSTOMER_OPERATIONS_DB_PATH",
            str(pathlib.Path(_ROOT) / "data" / "customer-operations" / "operations.db"),
        )
    )
    _context_assembler = ContextAssembler(
        max_input_tokens=int(os.getenv("CONTEXT_INPUT_BUDGET", "12000")),
        reserved_output_tokens=int(os.getenv("CONTEXT_OUTPUT_RESERVE", "1536")),
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        chroma_mode=chroma_mode,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        memory_token_budget=int(os.getenv("MEMORY_TOKEN_BUDGET", "6000")),
        compression_threshold=float(os.getenv("MEMORY_COMPRESSION_THRESHOLD", "0.70")),
        summary_max_tokens=int(os.getenv("MEMORY_SUMMARY_MAX_TOKENS", "1200")),
        fact_idle_seconds=float(os.getenv("MEMORY_FACT_IDLE_SECONDS", "300")),
        fact_batch_turns=int(os.getenv("MEMORY_FACT_BATCH_TURNS", "3")),
        fact_worker_poll_seconds=float(os.getenv("MEMORY_FACT_WORKER_POLL_SECONDS", "5")),
        model_profile=_model_policy.profile(ModelRole.MEMORY),
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        approval_mode=ApprovalMode(os.getenv("TOOL_APPROVAL_MODE", "default")),
        trace_recorder=_trace_recorder,
        max_output_chars=int(os.getenv("TOOL_OUTPUT_MAX_CHARS", "4000")),
        rewrite_model_profile=_model_policy.profile(ModelRole.REWRITE),
        rerank_model_profile=_model_policy.profile(ModelRole.RERANK),
        execution_store=_run_store,
    )
    _grounded_answer_generator = GroundedAnswerGenerator(
        _tool_manager.llm_client, _model_policy.profile(ModelRole.SYNTHESIS),
    )
    _knowledge_base = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        chroma_mode=chroma_mode,
        chunk_max_tokens=int(os.getenv("RAG_CHUNK_MAX_TOKENS", "512")),
        chunk_overlap_tokens=int(os.getenv("RAG_CHUNK_OVERLAP_TOKENS", "64")),
        chunk_strategy=os.getenv("RAG_CHUNK_STRATEGY", "fixed_tokens"),
        retrieval_rrf_k=int(bootstrap_rag_policy["rrf_k"]),
        retrieval_vector_weight=float(bootstrap_rag_policy["vector_weight"]),
        retrieval_lexical_weight=float(bootstrap_rag_policy["lexical_weight"]),
        sparse_index_path=os.getenv("RAG_SPARSE_INDEX_PATH", ""),
    )
    logger.info(f"知识库已加载: {await _knowledge_base.doc_count_async()} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        """知识检索不可用时返回可诊断降级信息，但不冒充真实业务证据。"""
        query = params.get("query", "")
        return [{
            "title": "知识库降级结果",
            "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试，或转人工客服确认。",
            "score": 0.0,
            "fallback": True,
            "error": error,
        }]

    _tool_manager.register(Tool(
        name="knowledge_search",
        description="搜索公共业务知识库（Raw/Standalone + BM25/Dense 加权 RRF）",
        handler=_knowledge_base.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        fallback=knowledge_fallback,
        allowed_agents=("general", "technical", "billing", "account_security"),
        read_only=True,
    ))

    async def memory_search(params: Dict[str, Any], context: Optional[Dict[str, Any]]):
        """在当前用户边界内执行混合长期记忆检索。"""
        context = context or {}
        user_id = str(context.get("user_id") or "").strip()
        if not user_id:
            raise ValueError("memory_search requires trusted user_id context")
        hits = await _memory.search_long_term(
            user_id,
            str(params.get("query") or ""),
            top_k=min(max(int(params.get("top_k", 5)), 1), 10),
        )
        return [{**hit.to_dict(), "content": hit.content} for hit in hits]

    _tool_manager.register(Tool(
        name="memory_search",
        description="检索当前用户的跨会话长期记忆；适合核对历史订单号、错误码和偏好",
        handler=memory_search,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
        allowed_agents=("general", "technical", "billing", "account_security"),
        read_only=True,
    ))
    for ticket_tool in ticket_tools(_ticket_service):
        _tool_manager.register(ticket_tool)
    for operation_tool in customer_operation_tools(_customer_operations):
        _tool_manager.register(operation_tool)
    _orchestrator.set_tool_manager(_tool_manager)

    def validate_bundle_activation(bundle: AgentBundle) -> tuple[str, ...]:
        """激活只接受当前进程真正能执行的模型和工具描述目标。"""
        errors = []
        if dict(bundle.model_policy) != _model_policy.to_dict():
            errors.append("model_policy requires a matching runtime deployment")
        unknown_tools = set(bundle.tool_descriptions) - set(_tool_manager.registered_tool_names)
        if unknown_tools:
            errors.append(f"unknown tool descriptions: {sorted(unknown_tools)}")
        return tuple(errors)

    _rollout_manager = RolloutManager(
        _bundle_registry,
        bucket_salt=(os.getenv("ROLLOUT_BUCKET_SALT") or os.getenv("AUTH_JWT_SECRET", "")),
        activation_validator=validate_bundle_activation,
        soft_policy=SoftRollbackPolicy(
            min_candidate_samples=int(os.getenv("ROLLOUT_SOFT_MIN_CANDIDATE_SAMPLES", "100")),
            min_baseline_samples=int(os.getenv("ROLLOUT_SOFT_MIN_BASELINE_SAMPLES", "100")),
            max_reject_rate_delta=float(os.getenv("ROLLOUT_MAX_REJECT_RATE_DELTA", "0.05")),
            max_latency_p95_ratio=float(os.getenv("ROLLOUT_MAX_LATENCY_P95_RATIO", "1.20")),
            max_cost_mean_ratio=float(os.getenv("ROLLOUT_MAX_COST_MEAN_RATIO", "1.20")),
        ),
    )

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
        judge_model_profile=_model_policy.profile(ModelRole.JUDGE),
    )

    await _memory.start()
    await _ticket_service.start()
    logger.info("DialogPilot 已就绪")
    try:
        yield
    finally:
        if _monitor is not None:
            await _monitor.stop()
        if _ticket_service is not None:
            await _ticket_service.close()
        if _memory is not None:
            await _memory.close()
        if _knowledge_base is not None and hasattr(_knowledge_base, "close"):
            await asyncio.to_thread(_knowledge_base.close)
        # lifespan 结束后不留下指向已关闭资源的进程全局引用。
        _orchestrator = None
        _memory = None
        _knowledge_base = None
        _tool_manager = None
        _monitor = None
        _evaluator = None
        _skill_manager = None
        _answer_verifier = None
        _ticket_service = None
        _response_delivery = None
        _badcase_registry = None
        _customer_operations = None
        _context_assembler = None
        _authenticator = None
        _model_policy = None
        _run_store = None
        _bundle_registry = None
        _proposal_generator = None
        _rollout_manager = None
        logger.info("DialogPilot 已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="DialogPilot 智能客服",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)


@app.middleware("http")
async def trace_request(request: FastAPIRequest, call_next):
    """为每个 HTTP 请求建立可跨 asyncio Task 传播的 TraceId。"""
    requested = (request.headers.get("x-trace-id") or "").strip()
    trace_id = requested if re.fullmatch(r"[A-Za-z0-9._-]{8,128}", requested) else uuid.uuid4().hex
    with trace_scope(trace_id):
        with _trace_recorder.span(
            f"http.{request.method.lower()}",
            kind="server",
            attributes={"http.method": request.method, "http.path": request.url.path},
        ):
            response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    """聊天入口的外部请求合同。"""
    message:     str = Field(min_length=1, max_length=10000)
    user_id:     Optional[str] = Field(default=None, min_length=1, max_length=200)
    conv_id:     Optional[str] = None
    request_id:  Optional[str] = Field(default=None, max_length=128)


class ChatResponse(BaseModel):
    """用户可见回答及路由、校验、工单等诊断投影。"""
    request_id:  str
    trace_id: str = ""
    conv_id:     str
    response_id: str
    response_seq: int
    delivery_status: DeliveryStatus = DeliveryStatus.SELECTED
    response:    str
    intent:      str
    intent_group: str = "other"
    agent_type:  str
    agent_types: List[str] = Field(default_factory=list)
    primary_agent: str = ""
    supporting_agents: List[str] = Field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    routing_disposition: str = "execute"
    synthesis_status: str = "single"
    synthesis_reason: str = ""
    synthesis_conflicts: List[str] = Field(default_factory=list)
    agent_outcomes: List[Dict[str, Any]] = Field(default_factory=list)
    task_plan: Dict[str, Any] = Field(default_factory=dict)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    execution_budget: Dict[str, Any] = Field(default_factory=dict)
    tool_audit: List[Dict[str, Any]] = Field(default_factory=list)
    memory_retrieval: List[Dict[str, Any]] = Field(default_factory=list)
    escalated:   bool
    latency_ms:  float
    knowledge_used: bool = False
    knowledge_draft_citations: List[str] = Field(default_factory=list)
    knowledge_citations: List[str] = Field(default_factory=list)
    knowledge_claims: List[Dict[str, Any]] = Field(default_factory=list)
    knowledge_conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    knowledge_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    knowledge_generation_status: str = "not_used"
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    intent_confidence: float = 0.0
    intent_source_scores: Dict[str, float] = Field(default_factory=dict)
    intent_prediction_id: str = ""
    intent_classifier_fingerprint: str = ""
    verification_status: str
    verified: bool
    grounded: bool
    verification_reason: str = ""
    verification_reason_code: str = ""
    ticket_id: Optional[str] = None
    ticket_status: Optional[str] = None
    handoff_created: bool = False
    bundle_version: str = "unversioned"
    rollout_stage: str = "active"
    awaiting_approval: bool = False
    react_run_ids: List[str] = Field(default_factory=list)
    pending_approval_call_ids: List[str] = Field(default_factory=list)


class ReactResumeInput(BaseModel):
    """宿主对持久待审批调用做显式决策；不接受模型传入 token。"""

    approved: bool


class AgentBundleInput(BaseModel):
    """管理员可注册的候选策略；没有任何 Active 指针写入口。"""

    version: str = Field(min_length=1, max_length=128)
    base_version: str = Field(default="", max_length=128)
    prompts: Dict[str, str] = Field(default_factory=dict)
    few_shots: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    routing_policy: Dict[str, Any] = Field(default_factory=dict)
    retrieval_policy: Dict[str, Any] = Field(default_factory=dict)
    tool_descriptions: Dict[str, str] = Field(default_factory=dict)
    model_policy: Dict[str, Any] = Field(default_factory=dict)
    source_badcase_groups: List[str] = Field(default_factory=list)


class EvolutionProposalInput(BaseModel):
    """从一个已归因 Bad Case 组生成 4-8 个不可变候选。"""

    semantic_group_id: str = Field(min_length=1, max_length=160)
    candidate_count: int = Field(default=4, ge=4, le=8)


class CanaryPromotionInput(BaseModel):
    percent: Literal[5, 25]


class RollbackInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class RolloutSignalInput(BaseModel):
    bundle_version: str = Field(min_length=1, max_length=128)
    signal: Literal[
        "unauthorized_tool", "privacy_leak", "cross_user_retrieval", "wrong_write"
    ]
    request_id: str = Field(default="", max_length=160)


class TicketCreateRequest(BaseModel):
    """人工或外部系统主动创建工单的输入合同。"""
    idempotency_key: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    conv_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=10000)
    published_response: str = Field(default="", max_length=20000)
    reason: str = Field(min_length=1, max_length=5000)
    priority: TicketPriority = TicketPriority.NORMAL
    agent_type: str = "general"
    intent: str = "other"
    verification_status: str = "unknown"


class TicketStatusUpdate(BaseModel):
    """工单状态迁移请求；合法性仍由 TicketService 判断。"""
    status: TicketStatus
    actor: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=1000)
    assignee: Optional[str] = Field(default=None, max_length=200)


class ResponseAckRequest(BaseModel):
    """客户端可证明的单调回执；READ 隐含已经 DELIVERED。"""
    status: Literal["delivered", "read"]


class BadCaseFeedbackRequest(BaseModel):
    """认证用户提交的负反馈；内容只作为待审核 observation。"""
    request_id: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(default="", max_length=128)
    category: Literal[
        "incorrect", "incomplete", "unsafe", "wrong_route",
        "bad_retrieval", "security_false_positive", "other",
    ]
    message: str = Field(default="", max_length=10000)
    published_response: str = Field(default="", max_length=12000)
    correction: str = Field(default="", max_length=5000)
    prediction_id: str = Field(default="", max_length=64)
    suggested_intent: Optional[IntentCategory] = None


class IntentFeedbackReviewRequest(BaseModel):
    """管理员对候选意图标签做一次不可变裁决。"""
    decision: Literal["approved", "rejected"]
    approved_intent: Optional[IntentCategory] = None
    dataset_version: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=1000)


class BadCaseTransitionRequest(BaseModel):
    """管理员请求状态迁移；证据充分性仍由 Registry 强制。"""
    status: BadCaseStatus
    note: str = Field(default="", max_length=1000)
    root_cause: Optional[str] = Field(default=None, max_length=4000)
    owner_module: Optional[str] = Field(default=None, max_length=240)
    eval_layer: Optional[Literal["intent", "routing", "retrieval", "stateful"]] = None
    expected_behavior: Optional[Dict[str, Any]] = None
    reproduction: Optional[Dict[str, Any]] = None
    fixed_by_commit: Optional[str] = Field(default=None, max_length=80)


class ConversationFinalizeResponse(BaseModel):
    """会话显式结束后的幂等归档结果。"""
    conv_id: str
    archived_messages: int
    finalized: bool
    already_empty: bool = False
    facts_flushed: bool = True


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """汇总依赖就绪状态和运行时统计，不承担业务健康修复。"""
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    storage = {
        "memory": _memory.storage_backend if _memory is not None else None,
        "knowledge": _knowledge_base.storage_backend if _knowledge_base is not None else None,
    }
    active_bundle = (
        await asyncio.to_thread(_bundle_registry.active)
        if _bundle_registry is not None else None
    )
    return {
        "status": "ok",
        "agents": _orchestrator.get_stats(),
        "tools": _tool_manager.get_stats() if _tool_manager is not None else {},
        "storage": storage,
        "model_policy": _model_policy.to_dict() if _model_policy is not None else None,
        "input_security": _input_security_guard.get_stats(),
        "intent_recognizer": _orchestrator.intent_runtime(active_bundle),
        "badcases": _badcase_registry.stats() if _badcase_registry is not None else {},
        "memory_fact_jobs": _memory.fact_job_stats if _memory is not None else {},
        "response_delivery": _response_delivery.stats() if _response_delivery is not None else {},
        "ticket_outbox": _ticket_service.outbox_stats() if _ticket_service is not None else {},
    }


@app.get("/skills", tags=["Skills"])
async def skills_summary(_principal: Principal = Depends(_admin_principal)):
    """查看当前已加载的 Skills，便于确认热加载结果和排查解析错误。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    return _skill_manager.summary()


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills(_principal: Principal = Depends(_admin_principal)):
    """运行时重新扫描 Skill 目录，不需要重启服务。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    _skill_manager.reload()
    if _orchestrator is not None:
        _orchestrator.set_skill_manager(_skill_manager)
    return _skill_manager.summary()


def _subject_for_request(requested_user_id: Optional[str], principal: Principal) -> str:
    """身份只由 Principal 决定；保留 user_id 仅用于检测旧客户端伪造/配置错误。"""
    if requested_user_id and requested_user_id != principal.subject:
        raise HTTPException(403, "请求 user_id 与认证身份不一致")
    return principal.subject


def _enforce_user_input_security(message: str) -> None:
    """在消息进入记忆、检索、LLM 或工具前阻断高置信直接注入。"""
    decision = _input_security_guard.analyze(message)
    with _trace_recorder.span(
        "security.user_input",
        kind="guardrail",
        attributes={
            "security.action": decision.action.value,
            "security.categories": list(decision.categories),
            "security.risk_score": decision.risk_score,
            "security.input_fingerprint": decision.input_fingerprint[:16],
        },
    ):
        pass
    if not decision.blocked:
        return
    logger.warning(
        "阻断高置信 Prompt Injection trace_id=%s categories=%s fingerprint=%s",
        current_trace_id(), ",".join(decision.categories), decision.input_fingerprint[:16],
    )
    raise HTTPException(
        400,
        detail={
            "error": "prompt_injection_detected",
            "trace_id": current_trace_id(),
            "message": "请求包含试图改变系统规则、获取内部指令或伪造授权的内容，已被安全策略阻止。",
        },
    )


async def _active_ticket_context(user_id: str) -> Optional[ContextSection]:
    """把 TicketService 未关闭工单投影为有界上下文，不复制其状态权威。"""
    if _ticket_service is None:
        return None
    try:
        tickets = await asyncio.to_thread(
            _ticket_service.list_active_tickets,
            user_id=user_id,
            limit=3,
        )
    except Exception as exc:
        logger.warning("读取未关闭工单上下文失败: %s", exc)
        return None
    if not tickets:
        return None
    content = json.dumps({
        "authority": "TicketService",
        "tickets": [
            {
                "ticket_id": ticket.ticket_id,
                "status": ticket.status.value,
                "priority": ticket.priority.value,
                "intent": ticket.intent,
                "question": ticket.question,
                "published_response": ticket.published_response,
                "assignee": ticket.assignee,
                "updated_at": ticket.updated_at,
            }
            for ticket in tickets
        ],
    }, ensure_ascii=False, sort_keys=True)
    return ContextSection(
        tag="active_tickets",
        description=(
            "TicketService 提供的当前客服事项状态；状态字段高于历史对话和摘要，"
            "实时业务工具结果仍是订单、退款和账户事实的最高权威"
        ),
        content=content,
        priority=90,
    )


def _public_agent_outcomes(outcomes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """用户响应只保留执行证据，不发布 candidate、内部错误或实例标识。"""
    allowed = {
        "task_id", "required", "agent_type", "responding_agent_type", "status",
        "is_primary", "confidence", "latency_ms", "escalate", "react_status", "react_steps",
        "react_run_id", "pending_approval_call_ids",
    }
    projected = []
    for outcome in outcomes:
        item = {key: outcome[key] for key in allowed if key in outcome}
        if outcome.get("status") != "success":
            item["error_code"] = f"agent_{outcome.get('status', 'unknown')}"
        projected.append(item)
    return projected


async def _verify_for_publication(
    verifier: Any,
    question: str,
    candidate: str,
    context: str,
    **evidence: Any,
) -> VerificationResult:
    """API 发布边界把任意 verifier 合同违约收敛为 UNKNOWN。"""
    try:
        result = await verifier.verify(question, candidate, context, **evidence)
        if not isinstance(result, VerificationResult):
            raise TypeError("verifier returned unsupported result")
        return result
    except Exception as exc:
        return VerificationResult(
            status=VerificationStatus.UNKNOWN,
            grounded=False,
            need_escalation=True,
            reason=f"verification boundary failure: {type(exc).__name__}",
            reason_code=VerificationReasonCode.VERIFIER_UNAVAILABLE,
        )


def _publish_candidate(candidate: str, verification: VerificationResult) -> str:
    """只有 PASS candidate 能越过用户可见边界。"""
    if verification.publishable:
        return candidate
    return "当前回答未通过可信度校验，已转交人工进一步确认。"


def _policy_terminal_verification(result: Any) -> Optional[VerificationResult]:
    """确定性 Planner 终态由代码策略拥有，不再调用回答模型或创建人工工单。"""
    raw_disposition = getattr(result, "routing_disposition", None)
    disposition = getattr(raw_disposition, "value", raw_disposition)
    if disposition not in {"clarify", "out_of_scope"}:
        return None
    return VerificationResult(
        status=VerificationStatus.PASS,
        grounded=True,
        need_escalation=False,
        reason=f"deterministic planner terminal: {disposition}",
        reason_code=VerificationReasonCode.POLICY_TERMINAL,
    )


def _select_publication_candidate(
    agent_response: str,
    knowledge: "KnowledgeContextResult",
    tool_audit: List[Dict[str, Any]],
    *,
    approval_pending: bool,
) -> tuple[str, bool]:
    """纯公共知识问答发布已校验 grounded result；混合业务事实仍由 Agent 合成。"""
    business_tool_used = any(
        str(record.get("tool") or record.get("tool_name") or "") != "knowledge_search"
        for record in tool_audit
    )
    knowledge_is_final = bool(
        knowledge.answer
        and knowledge.generation_status in {"grounded_draft", "abstained"}
        and not business_tool_used
        and not approval_pending
    )
    return (knowledge.answer if knowledge_is_final else agent_response), knowledge_is_final


def _badcase_versions(bundle: Optional[AgentBundle] = None) -> Dict[str, Any]:
    """生成不含密钥的复现版本投影。"""
    raw_skill_rows = (_skill_manager.summary().get("skills") or []) if _skill_manager else []
    skill_rows = [
        {key: value for key, value in row.items() if key != "path"}
        for row in raw_skill_rows
    ]
    skill_sha = hashlib.sha256(
        json.dumps(skill_rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    versions = {
        "commit_sha": os.getenv("GIT_COMMIT_SHA", "unknown")[:80],
        "model_policy": _model_policy.to_dict() if _model_policy is not None else {},
        "skill_registry_sha256": skill_sha,
        "knowledge": _knowledge_base.storage_backend if _knowledge_base is not None else {},
    }
    if bundle is not None:
        versions["agent_bundle_version"] = bundle.version
        versions["agent_bundle_sha256"] = bundle.content_hash
    return versions


async def _record_intent_prediction(
    *,
    intent_result: Any,
    request_id: str,
    conv_id: str,
    user_id: str,
    message: str,
    bundle: AgentBundle,
) -> Optional[Any]:
    """把分类结果写成不可变质量事实；遥测故障不改变聊天业务结果。"""
    if _badcase_registry is None:
        return None
    classifier_fingerprint = str(
        getattr(intent_result, "classifier_fingerprint", "") or ""
    )
    input_fingerprint = str(getattr(intent_result, "input_fingerprint", "") or "")
    if not classifier_fingerprint or not input_fingerprint:
        logger.warning(
            "意图识别结果缺少版本证据，跳过预测记录 request_id=%s", request_id,
        )
        return None
    try:
        return await asyncio.to_thread(
            _badcase_registry.record_intent_prediction,
            request_id=request_id,
            trace_id=current_trace_id(),
            conv_id=conv_id,
            user_id=user_id,
            input_fingerprint=input_fingerprint,
            sanitized_input=message,
            predicted_intent=intent_result.intent.value,
            confidence=intent_result.confidence,
            source_scores=intent_result.source_scores,
            classifier_fingerprint=classifier_fingerprint,
            bundle_version=bundle.version,
        )
    except Exception:
        logger.exception("记录意图预测失败 request_id=%s", request_id)
        return None


async def _observe_badcase(**observation: Any) -> None:
    """自动捕获不得改变主请求结果；失败只进入服务日志。"""
    if _badcase_registry is None:
        return
    try:
        versions = observation.pop("versions", None)
        await asyncio.to_thread(
            _badcase_registry.observe,
            versions=versions if versions is not None else _badcase_versions(),
            **observation,
        )
    except Exception:
        logger.exception(
            "Bad Case observation 持久化失败 trace_id=%s symptom=%s",
            current_trace_id(), observation.get("symptom_code", "unknown"),
        )


async def _capture_chat_badcases(
    *,
    req: ChatRequest,
    user_id: str,
    request_id: str,
    result: Any,
    verification: VerificationResult,
    published_response: str,
    tool_audit: List[Dict[str, Any]],
    bundle: Optional[AgentBundle] = None,
    approval_pending: bool = False,
) -> None:
    """把发布、Coverage 与工具终态投影为去重候选，不复制未发布 candidate。"""
    resolved_bundle = bundle or build_default_bundle(
        _model_policy.to_dict() if _model_policy is not None else {}
    )
    common = {
        "user_id": user_id,
        "sanitized_input": req.message,
        "trace_id": current_trace_id(),
        "request_id": request_id,
        "published_response": published_response,
        "versions": _badcase_versions(resolved_bundle),
    }
    task_ids = [
        str(task.get("task_id") or "")
        for task in (result.task_plan.get("tasks") or [])
        if isinstance(task, dict) and task.get("task_id")
    ]
    call_ids = [
        str(record.get("call_id") or "")
        for record in tool_audit if record.get("call_id")
    ]
    tool_hash = (
        _tool_manager.registry_fingerprint(dict(resolved_bundle.tool_descriptions))
        if _tool_manager is not None else ""
    )

    def evidence_with_envelope(symptom: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        envelope = EvolutionEnvelope.from_execution(
            request_id=request_id,
            trace_id=current_trace_id(),
            bundle=resolved_bundle,
            tool_registry_hash=tool_hash,
            producer_agent_keys=getattr(result, "producer_agent_keys", ()),
            task_ids=task_ids,
            tool_call_ids=call_ids,
            verification=verification.status.value,
            badcase_group=symptom,
        )
        return {**evidence, "evolution_envelope": envelope.to_dict()}

    if not approval_pending and verification.status is not VerificationStatus.PASS:
        symptom = f"verification_{verification.status.value}"
        await _observe_badcase(
            **common,
            source="verifier",
            stage=BadCaseStage.VERIFICATION,
            severity=BadCaseSeverity.P1,
            symptom_code=symptom,
            evidence=evidence_with_envelope(symptom, {
                "reason_code": verification.reason_code.value,
                "grounded": verification.grounded,
                "need_escalation": verification.need_escalation,
                "intent": result.intent.value if result.intent else "other",
            }),
        )
    policy_terminal = _policy_terminal_verification(result) is not None
    if (
        not approval_pending
        and not policy_terminal
        and not bool(result.coverage.get("complete", False))
    ):
        symptom = "required_task_coverage_incomplete"
        await _observe_badcase(
            **common,
            source="coverage_gate",
            stage=BadCaseStage.COVERAGE,
            severity=BadCaseSeverity.P1,
            symptom_code=symptom,
            evidence=evidence_with_envelope(
                symptom, {"coverage": result.coverage, "task_plan": result.task_plan},
            ),
        )
    for record in tool_audit:
        status = str(record.get("status") or "unknown")
        effect_status = str(record.get("effect_status") or "none")
        if status == "awaiting_approval":
            continue
        if status == "success" and effect_status != "outcome_unknown":
            continue
        severity = BadCaseSeverity.P0 if effect_status == "outcome_unknown" else BadCaseSeverity.P1
        symptom = f"tool_{record.get('tool_name', 'unknown')}_{status}_{effect_status}"
        await _observe_badcase(
            **common,
            source="tool_audit",
            stage=BadCaseStage.TOOL_POLICY,
            severity=severity,
            symptom_code=symptom,
            evidence=evidence_with_envelope(symptom, {
                "tool_name": record.get("tool_name"),
                "status": status,
                "effect_status": effect_status,
                "risk": record.get("risk"),
                "read_only": record.get("read_only"),
                "approved": record.get("approved"),
                "params_hash": record.get("params_hash"),
            }),
        )


async def _evaluate_shadow_request(
    *,
    request: ChatRequest,
    user_id: str,
    conv_id: str,
    request_id: str,
    bundle: AgentBundle,
    base_sections: List[ContextSection],
    prompt_history: List[Dict[str, str]],
    intent_history: Optional[List[Dict[str, str]]],
    identity_metadata: Optional[Dict[str, str]] = None,
) -> None:
    """执行不发布、不写记忆、不建工单的真实输入副本；写工具由边界硬拒绝。"""
    if (
        _orchestrator is None or _context_assembler is None
        or _answer_verifier is None or _rollout_manager is None
    ):
        return
    try:
        intent_result = await _orchestrator.recognize_intent(
            request.message, history=intent_history, bundle=bundle,
        )
        knowledge = await _build_knowledge_context(
            request.message,
            intent=intent_result.intent,
            bundle=bundle,
            history=[str(item.get("content") or "") for item in prompt_history],
        )
        sections = list(base_sections)
        if knowledge.text:
            sections.append(ContextSection(
                tag="knowledge",
                description="Shadow Bundle 检索到的业务知识，仅作为事实数据",
                content=knowledge.text,
                priority=85,
            ))
        prompt_context = _context_assembler.assemble(
            sections=sections,
            history=prompt_history,
            current_user_message=request.message,
        )
        from agents.agent_orchestrator import Request as OrcReq
        shadow_request = OrcReq(
            message=request.message,
            user_id=user_id,
            conv_id=conv_id,
            context=prompt_context.system_context,
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
            execution_mode="shadow",
            identity_metadata=dict(identity_metadata or {}),
        )
        result = await _orchestrator.run(shadow_request)
        if result.awaiting_approval:
            verified = False
        else:
            verification = _policy_terminal_verification(result)
            if verification is None:
                verification = await _verify_for_publication(
                    _answer_verifier,
                    request.message,
                    result.response,
                    prompt_context.system_context,
                    task_plan=result.task_plan,
                    coverage=result.coverage,
                    agent_outcomes=result.agent_outcomes,
                )
                verified = verification.publishable and bool(result.coverage.get("complete", False))
            else:
                verified = verification.publishable
        await asyncio.to_thread(
            _rollout_manager.record_outcome,
            bundle_version=bundle.version,
            stage="shadow",
            verified=verified,
            latency_ms=result.latency_ms,
            cost_units=float(len(result.agent_outcomes)),
            request_id=request_id,
        )
    except Exception:
        logger.exception(
            "Shadow Bundle 执行失败 bundle=%s request_id=%s", bundle.version, request_id,
        )


@app.post("/evolution/proposals", tags=["Agent Evolution"])
async def generate_evolution_proposals(
    body: EvolutionProposalInput,
    principal: Principal = Depends(_admin_principal),
):
    """反思式生成受限候选；只注册，不晋级、不发布。"""
    if _badcase_registry is None or _bundle_registry is None or _proposal_generator is None:
        raise HTTPException(503, "Agent Evolution 服务未就绪")
    cases = await asyncio.to_thread(
        _badcase_registry.list,
        semantic_group_id=body.semantic_group_id,
        limit=200,
    )
    clusters = BadCaseMiner().cluster(cases)
    cluster = next((item for item in clusters if item.group_id == body.semantic_group_id), None)
    if cluster is None:
        raise HTTPException(404, {"code": "evolution_group_not_found"})
    attribution = CreditAttributor().attribute(cluster)
    if not attribution.evolvable:
        raise HTTPException(409, {
            "code": "owner_not_auto_evolvable",
            "attribution": attribution.to_dict(),
        })
    base = await asyncio.to_thread(_bundle_registry.active)
    try:
        candidates = await _proposal_generator.generate(
            base=base,
            cluster=cluster,
            attribution=attribution,
            candidate_count=body.candidate_count,
        )
        for candidate in candidates:
            await asyncio.to_thread(
                _bundle_registry.register,
                candidate,
                actor=f"proposal:{principal.subject}",
            )
    except BundleContractError as exc:
        raise HTTPException(422, {"code": "invalid_evolution_proposal", "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Evolution proposal 生成失败 group=%s", body.semantic_group_id)
        raise HTTPException(502, {"code": "proposal_provider_failed"}) from exc
    return {
        "base_version": base.version,
        "attribution": attribution.to_dict(),
        "candidates": [
            {"version": item.version, "content_hash": item.content_hash}
            for item in candidates
        ],
        "active_changed": False,
    }


@app.get("/evolution/bundles", tags=["Agent Evolution"])
async def list_agent_bundles(
    limit: int = Query(default=100, ge=1, le=500),
    _principal: Principal = Depends(_admin_principal),
):
    """列出不可变 Bundle 元数据；不把完整 Prompt 暴露到普通聊天接口。"""
    if _bundle_registry is None:
        raise HTTPException(503, "Agent Bundle 服务未就绪")
    return {"items": await asyncio.to_thread(_bundle_registry.list, limit)}


@app.get("/evolution/bundles/active", tags=["Agent Evolution"])
async def get_active_agent_bundle(_principal: Principal = Depends(_admin_principal)):
    """返回当前 Active Bundle 的管理投影。"""
    if _bundle_registry is None:
        raise HTTPException(503, "Agent Bundle 服务未就绪")
    bundle = await asyncio.to_thread(_bundle_registry.active)
    return {"bundle": bundle.to_dict(), "content_hash": bundle.content_hash}


@app.post("/evolution/bundles", status_code=201, tags=["Agent Evolution"])
async def register_agent_bundle(
    body: AgentBundleInput,
    principal: Principal = Depends(_admin_principal),
):
    """只注册候选；晋级和发布必须由后续 Graduation/Rollout Owner 完成。"""
    if _bundle_registry is None:
        raise HTTPException(503, "Agent Bundle 服务未就绪")
    try:
        bundle = AgentBundle(**body.model_dump())
        registered = await asyncio.to_thread(
            _bundle_registry.register, bundle, actor=principal.subject,
        )
    except BundleNotFoundError as exc:
        raise HTTPException(404, {"code": "base_bundle_not_found", "version": str(exc)}) from exc
    except BundleConflictError as exc:
        raise HTTPException(409, {"code": "bundle_version_conflict", "message": str(exc)}) from exc
    except BundleContractError as exc:
        raise HTTPException(422, {"code": "invalid_bundle", "message": str(exc)}) from exc
    return {"bundle": registered.to_dict(), "content_hash": registered.content_hash, "active": False}


def _chat_application() -> ChatApplication:
    """Compose the application boundary from the current lifespan-owned services."""
    return ChatApplication(
        ChatServices(
            orchestrator=_orchestrator,
            memory=_memory,
            answer_verifier=_answer_verifier,
            ticket_service=_ticket_service,
            response_delivery=_response_delivery,
            context_assembler=_context_assembler,
            bundle_registry=_bundle_registry,
            rollout_manager=_rollout_manager,
            tool_manager=_tool_manager,
            trace_recorder=_trace_recorder,
        ),
        ChatOperations(
            active_ticket_context=_active_ticket_context,
            build_knowledge_context=_build_knowledge_context,
            capture_badcases=_capture_chat_badcases,
            evaluate_shadow=_evaluate_shadow_request,
            handoff_priority=_handoff_priority,
            policy_terminal_verification=_policy_terminal_verification,
            publish_candidate=_publish_candidate,
            public_agent_outcomes=_public_agent_outcomes,
            record_intent_prediction=_record_intent_prediction,
            select_publication_candidate=_select_publication_candidate,
            trace_id=current_trace_id,
            verify_for_publication=_verify_for_publication,
        ),
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, principal: Principal = Depends(_chat_principal)):
    """Validate the HTTP request and map the protocol-neutral application outcome."""
    _enforce_user_input_security(req.message)
    command = ChatCommand(
        message=req.message,
        user_id=_subject_for_request(req.user_id, principal),
        conv_id=req.conv_id,
        request_id=req.request_id,
    )
    outcome = await _chat_application().handle(command)
    if isinstance(outcome, Completed):
        return ChatResponse.model_validate(outcome.response)
    if isinstance(outcome, Failed):
        status = 503 if outcome.retryable else 500
        raise HTTPException(status, {
            "error": outcome.code,
            "retryable": outcome.retryable,
            "correlation_id": outcome.correlation_id,
        })
    if isinstance(outcome, Rejected):
        raise HTTPException(422, {
            "error": outcome.code,
            "message": outcome.safe_message,
        })
    raise HTTPException(500, {
        "error": "unsupported_chat_outcome",
        "retryable": False,
        "outcome": type(outcome).__name__,
    })


@app.get("/agent-runs/{run_id}", tags=["Agent Run"])
async def get_agent_run(
    run_id: str,
    principal: Principal = Depends(_chat_principal),
):
    """只向 Run 所属的认证用户返回脱敏状态。"""
    if _orchestrator is None:
        raise HTTPException(503, "Agent 服务未就绪")
    from agents.run_store import RunAccessDeniedError, RunNotFoundError

    try:
        checkpoint = await asyncio.to_thread(
            _orchestrator.get_react_run,
            run_id,
            user_id=principal.subject,
        )
    except RunNotFoundError as exc:
        raise HTTPException(404, {"code": "run_not_found"}) from exc
    except RunAccessDeniedError as exc:
        raise HTTPException(403, {"code": "run_access_denied"}) from exc
    return checkpoint.to_public_dict()


@app.post("/agent-runs/{run_id}/resume", tags=["Agent Run"])
async def resume_agent_run(
    run_id: str,
    body: ReactResumeInput,
    principal: Principal = Depends(_tool_approval_principal),
):
    """审批决策绑定 JWT Principal 和原 Run，恢复后仍经过发布校验。"""
    if _orchestrator is None or _answer_verifier is None:
        raise HTTPException(503, "Agent 服务未就绪")
    from agents.run_store import (
        RunAccessDeniedError,
        RunNotFoundError,
        RunTransitionError,
        RunVersionConflictError,
    )

    try:
        before = await asyncio.to_thread(
            _orchestrator.get_react_run,
            run_id,
            user_id=principal.subject,
        )
        result = await _orchestrator.resume_react(
            run_id,
            user_id=principal.subject,
            approved=body.approved,
            actor=principal.subject,
        )
        after = await asyncio.to_thread(
            _orchestrator.get_react_run,
            run_id,
            user_id=principal.subject,
        )
    except RunNotFoundError as exc:
        raise HTTPException(404, {"code": "run_not_found"}) from exc
    except RunAccessDeniedError as exc:
        raise HTTPException(403, {"code": "run_access_denied"}) from exc
    except (RunTransitionError, RunVersionConflictError, ValueError) as exc:
        raise HTTPException(409, {"code": "run_not_resumable", "message": str(exc)}) from exc

    if result.success:
        task_id = before.task_id
        verification = await _verify_for_publication(
            _answer_verifier,
            str(before.execution_context.get("task_input") or ""),
            result.content,
            before.system,
            task_plan={"primary_task_id": task_id, "tasks": [{"task_id": task_id}]},
            coverage={
                "complete": True,
                "required_task_ids": [task_id],
                "completed_task_ids": [task_id],
                "unresolved_required_task_ids": [],
            },
            agent_outcomes=[{"task_id": task_id, "status": "success"}],
        )
        published = _publish_candidate(result.content, verification)
    else:
        verification = VerificationResult(
            status=VerificationStatus.UNKNOWN,
            grounded=False,
            need_escalation=False,
            reason=result.reason or "run did not produce a publishable completion",
            reason_code=(
                VerificationReasonCode.APPROVAL_REQUIRED
                if result.status.value == "waiting_approval"
                else VerificationReasonCode.INCOMPLETE
            ),
        )
        published = (
            "操作已暂停，仍在等待审批。"
            if result.status.value == "waiting_approval"
            else "操作未完成，未发布 Agent 候选内容，请根据 Run 状态重试或联系人工。"
        )
    return {
        **after.to_public_dict(),
        "response": published,
        "verification_status": verification.status.value,
        "verified": verification.publishable,
        "verification_reason_code": verification.reason_code.value,
    }


def _handoff_priority(urgency: Any, verification_status: str) -> TicketPriority:
    """把意图紧急度与发布校验结果投影为人工队列优先级。"""
    urgency_value = getattr(urgency, "value", urgency)
    urgency_name = str(getattr(urgency, "name", "")).lower()
    if urgency_name == "critical" or urgency_value in {"critical", 4}:
        return TicketPriority.CRITICAL
    if verification_status in {"reject", "unknown"}:
        return TicketPriority.HIGH
    return TicketPriority.NORMAL


@app.post(
    "/conversations/{conv_id}/finalize",
    response_model=ConversationFinalizeResponse,
    tags=["记忆"],
)
async def finalize_conversation(
    conv_id: str,
    principal: Principal = Depends(_chat_principal),
):
    """显式归档未达压缩阈值的短会话；并发写入时保留现场供幂等重试。"""
    if _memory is None:
        raise HTTPException(503, "记忆服务未就绪")
    result = await _memory.finalize_conversation(principal.subject, conv_id)
    if not result.get("finalized"):
        status_code = 409 if result.get("reason") == "concurrent_write" else 503
        raise HTTPException(status_code, {
            "error": result.get("reason", "finalize_failed"),
            "retryable": True,
        })
    return ConversationFinalizeResponse(conv_id=conv_id, **result)


@app.post("/responses/{response_id}/ack", tags=["回答送达"])
async def acknowledge_response(
    response_id: str,
    body: ResponseAckRequest,
    principal: Principal = Depends(_chat_principal),
):
    """由认证客户端确认已渲染或已读；重复与乱序 ACK 保持单调幂等。"""
    if _response_delivery is None:
        raise HTTPException(503, "回答送达服务未就绪")
    try:
        delivery = await asyncio.to_thread(
            _response_delivery.acknowledge,
            response_id,
            user_id=principal.subject,
            status=DeliveryStatus(body.status),
        )
    except ResponseNotFoundError as exc:
        # 不区分不存在和他人回答，避免 ID 枚举泄露。
        raise HTTPException(404, {"error": "response_not_found"}) from exc
    return delivery.to_public_dict()


@app.get("/conversations/{conv_id}/responses", tags=["回答送达"])
async def replay_responses(
    conv_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(_chat_principal),
):
    """客户端重连后按稳定 seq 续取遗漏回答，再逐条提交 ACK。"""
    if _response_delivery is None:
        raise HTTPException(503, "回答送达服务未就绪")
    deliveries = await asyncio.to_thread(
        _response_delivery.list_after,
        user_id=principal.subject,
        conv_id=conv_id,
        after_seq=after_seq,
        limit=limit,
    )
    return {
        "conv_id": conv_id,
        "responses": [delivery.to_public_dict() for delivery in deliveries],
        "last_seq": deliveries[-1].seq if deliveries else after_seq,
    }


@app.post("/tickets", tags=["人工工单"])
async def create_ticket(body: TicketCreateRequest, _principal: Principal = Depends(_admin_principal)):
    """Manually create an idempotent handoff ticket."""
    if _ticket_service is None:
        raise HTTPException(503, "工单服务未就绪")
    try:
        ticket, created = await asyncio.to_thread(
            _ticket_service.create_ticket,
            **body.model_dump(),
        )
        return {"created": created, "ticket": ticket.to_dict()}
    except IdempotencyConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/tickets", tags=["人工工单"])
async def list_tickets(
    user_id: Optional[str] = None,
    status: Optional[TicketStatus] = None,
    limit: int = Query(default=50, ge=1, le=200),
    _principal: Principal = Depends(_admin_principal),
):
    """List tickets with optional user and status filters."""
    if _ticket_service is None:
        raise HTTPException(503, "工单服务未就绪")
    tickets = await asyncio.to_thread(
        _ticket_service.list_tickets,
        user_id=user_id,
        status=status,
        limit=limit,
    )
    return {"tickets": [ticket.to_dict() for ticket in tickets], "count": len(tickets)}


@app.get("/tickets/{ticket_id}", tags=["人工工单"])
async def get_ticket(ticket_id: str, _principal: Principal = Depends(_admin_principal)):
    """Return a ticket together with its immutable transition history."""
    if _ticket_service is None:
        raise HTTPException(503, "工单服务未就绪")
    try:
        return await asyncio.to_thread(_ticket_service.get_ticket_view, ticket_id)
    except TicketNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.patch("/tickets/{ticket_id}/status", tags=["人工工单"])
async def update_ticket_status(
    ticket_id: str,
    body: TicketStatusUpdate,
    _principal: Principal = Depends(_admin_principal),
):
    """Apply one legal state transition and append an audit event."""
    if _ticket_service is None:
        raise HTTPException(503, "工单服务未就绪")
    try:
        ticket = await asyncio.to_thread(
            _ticket_service.transition,
            ticket_id,
            body.status,
            actor=body.actor,
            note=body.note,
            assignee=body.assignee,
        )
        return ticket.to_dict()
    except TicketNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(
            409,
            {
                "error": "invalid_ticket_transition",
                "current": exc.current.value,
                "target": exc.target.value,
            },
        ) from exc


@app.post("/feedback", tags=["质量闭环"])
async def submit_badcase_feedback(
    body: BadCaseFeedbackRequest,
    principal: Principal = Depends(_chat_principal),
):
    """用户点踩/纠错只创建待审核 observation，不能自行定义 Gold。"""
    if _badcase_registry is None:
        raise HTTPException(503, "Bad Case Registry 未就绪")
    if body.category == "wrong_route":
        if not body.prediction_id or body.suggested_intent is None:
            raise HTTPException(422, {
                "code": "intent_feedback_requires_prediction",
                "message": "wrong_route requires prediction_id and suggested_intent",
            })
        try:
            learning, case, created = await asyncio.to_thread(
                _badcase_registry.submit_intent_feedback,
                prediction_id=body.prediction_id,
                user_id=principal.subject,
                suggested_intent=body.suggested_intent.value,
                reason=body.correction,
                published_response=body.published_response,
                source="user_feedback",
            )
            return {
                "created": created,
                "badcase_id": case.badcase_id,
                "status": case.status.value,
                "occurrence_count": case.occurrence_count,
                "prediction_id": learning.prediction_id,
                "feedback_status": learning.feedback_status.value,
                "predicted_intent": learning.predicted_intent,
                "suggested_intent": learning.suggested_intent,
            }
        except BadCaseNotFoundError as exc:
            raise HTTPException(404, {"code": "intent_prediction_not_found"}) from exc
        except BadCaseContractError as exc:
            raise HTTPException(422, str(exc)) from exc
    stage_by_category = {
        "bad_retrieval": BadCaseStage.RETRIEVAL,
        "security_false_positive": BadCaseStage.INPUT_SECURITY,
    }
    stage = stage_by_category.get(body.category, BadCaseStage.VERIFICATION)
    try:
        case, created = await asyncio.to_thread(
            _badcase_registry.observe,
            source="user_feedback",
            stage=stage,
            severity=BadCaseSeverity.P1 if body.category == "unsafe" else BadCaseSeverity.P2,
            symptom_code=f"user_feedback_{body.category}",
            user_id=principal.subject,
            sanitized_input=body.message or f"[request:{body.request_id}]",
            trace_id=body.trace_id,
            request_id=body.request_id,
            published_response=body.published_response,
            evidence={"category": body.category, "correction": body.correction},
            versions=_badcase_versions(),
        )
        return {
            "created": created,
            "badcase_id": case.badcase_id,
            "status": case.status.value,
            "occurrence_count": case.occurrence_count,
        }
    except BadCaseContractError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/bad-cases", tags=["质量闭环"])
async def list_badcases(
    status: Optional[BadCaseStatus] = None,
    stage: Optional[BadCaseStage] = None,
    severity: Optional[BadCaseSeverity] = None,
    limit: int = Query(default=50, ge=1, le=200),
    _principal: Principal = Depends(_admin_principal),
):
    """管理员读取按生命周期和责任层筛选的 Bad Case 队列。"""
    if _badcase_registry is None:
        raise HTTPException(503, "Bad Case Registry 未就绪")
    cases = await asyncio.to_thread(
        _badcase_registry.list,
        status=status,
        stage=stage,
        severity=severity,
        limit=limit,
    )
    return {"bad_cases": [case.to_dict() for case in cases], "count": len(cases)}


@app.get("/bad-cases/{badcase_id}", tags=["质量闭环"])
async def get_badcase(
    badcase_id: str,
    _principal: Principal = Depends(_admin_principal),
):
    """返回当前事实及不可变迁移审计。"""
    if _badcase_registry is None:
        raise HTTPException(503, "Bad Case Registry 未就绪")
    try:
        return await asyncio.to_thread(_badcase_registry.get_view, badcase_id)
    except BadCaseNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.patch("/bad-cases/{badcase_id}/status", tags=["质量闭环"])
async def transition_badcase(
    badcase_id: str,
    body: BadCaseTransitionRequest,
    principal: Principal = Depends(_admin_principal),
):
    """迁移闭合状态；actor 永远来自签名身份而非请求正文。"""
    if _badcase_registry is None:
        raise HTTPException(503, "Bad Case Registry 未就绪")
    try:
        case = await asyncio.to_thread(
            _badcase_registry.transition,
            badcase_id,
            body.status,
            actor=principal.subject,
            note=body.note,
            root_cause=body.root_cause,
            owner_module=body.owner_module,
            eval_layer=body.eval_layer,
            expected_behavior=body.expected_behavior,
            reproduction=body.reproduction,
            fixed_by_commit=body.fixed_by_commit,
        )
        return case.to_dict()
    except BadCaseNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except BadCaseTransitionError as exc:
        raise HTTPException(409, {
            "error": "invalid_badcase_transition",
            "current": exc.current.value,
            "target": exc.target.value,
        }) from exc
    except BadCaseContractError as exc:
        raise HTTPException(422, {
            "error": "badcase_evidence_incomplete",
            "message": str(exc),
        }) from exc


@app.get("/intent-feedback", tags=["质量闭环"])
async def list_intent_feedback(
    status: Optional[IntentFeedbackStatus] = None,
    limit: int = Query(default=50, ge=1, le=200),
    _principal: Principal = Depends(_admin_principal),
):
    """管理员读取预测、待审纠正和已裁决标签，不向普通用户泄露样本。"""
    if _badcase_registry is None:
        raise HTTPException(503, "Bad Case Registry 未就绪")
    rows = await asyncio.to_thread(
        _badcase_registry.list_intent_learning,
        status=status,
        limit=limit,
    )
    return {"items": [row.to_dict() for row in rows], "count": len(rows)}


@app.get("/intent-predictions/{prediction_id}", tags=["质量闭环"])
async def get_intent_prediction(
    prediction_id: str,
    _principal: Principal = Depends(_admin_principal),
):
    """管理员按稳定 prediction_id 读取一次版本化预测事实。"""
    if _badcase_registry is None:
        raise HTTPException(503, "Bad Case Registry 未就绪")
    try:
        row = await asyncio.to_thread(
            _badcase_registry.get_intent_prediction, prediction_id,
        )
        return row.to_dict()
    except BadCaseNotFoundError as exc:
        raise HTTPException(404, {"code": "intent_prediction_not_found"}) from exc


@app.post("/intent-feedback/{prediction_id}/review", tags=["质量闭环"])
async def review_intent_feedback(
    prediction_id: str,
    body: IntentFeedbackReviewRequest,
    principal: Principal = Depends(_admin_principal),
):
    """人工裁决候选标签；批准不会绕过评测、Graduation 或 Rollout。"""
    if _badcase_registry is None:
        raise HTTPException(503, "Bad Case Registry 未就绪")
    if body.decision == "approved" and body.approved_intent is None:
        raise HTTPException(422, {
            "code": "approved_intent_required",
            "message": "approved decision requires approved_intent",
        })
    try:
        learning, case = await asyncio.to_thread(
            _badcase_registry.review_intent_feedback,
            prediction_id,
            decision=IntentFeedbackStatus(body.decision),
            actor=principal.subject,
            approved_intent=(
                body.approved_intent.value if body.approved_intent is not None else ""
            ),
            dataset_version=body.dataset_version,
            note=body.note,
        )
        return {
            "intent_learning": learning.to_dict(),
            "bad_case": case.to_dict(),
            "active_bundle_changed": False,
        }
    except BadCaseNotFoundError as exc:
        raise HTTPException(404, {"code": "intent_prediction_not_found"}) from exc
    except BadCaseContractError as exc:
        raise HTTPException(409, {
            "code": "intent_feedback_review_rejected",
            "message": str(exc),
        }) from exc


@dataclass(frozen=True)
class KnowledgeContextResult:
    text: str = ""
    used: bool = False
    citations: tuple[str, ...] = ()
    generation_status: str = "not_used"
    answer: str = ""
    claims: tuple[Dict[str, Any], ...] = ()
    conflicts: tuple[Dict[str, Any], ...] = ()
    abstained: bool = False
    reason: str = ""
    evidence_pack: Optional[EvidencePack] = None

    def __iter__(self):
        """Keep the historical ``text, used`` internal call contract during migration."""
        yield self.text
        yield self.used


def _rag_cache_scope(policy_scope: str) -> str:
    """绑定检索策略与当前 corpus Manifest，知识变更后旧结果自然失效。"""
    fingerprint = "unversioned-index"
    if _knowledge_base is not None and hasattr(_knowledge_base, "index_manifest"):
        fingerprint = str(_knowledge_base.index_manifest.get("manifest_fingerprint") or fingerprint)
    return f"{policy_scope}:{fingerprint}"


async def _build_knowledge_context(
    message: str,
    intent=None,
    top_k: int = 5,
    bundle: Optional[AgentBundle] = None,
    history: List[str] | tuple[str, ...] = (),
) -> KnowledgeContextResult:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    这里复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return KnowledgeContextResult()
    if not _should_use_knowledge(message, intent=intent):
        return KnowledgeContextResult()
    try:
        policy = {
            **DEFAULT_RAG_RETRIEVAL_POLICY,
            **(dict(bundle.retrieval_policy) if bundle is not None else {}),
        }
        resolved_top_k = int(policy.get("top_k", top_k))
        result = await _tool_manager.search_with_rewrite(
            "knowledge_search",
            message,
            top_k=resolved_top_k,
            context={
                "retrieval_policy": policy,
                "query_history": list(history[-8:]),
                "cache_scope": _rag_cache_scope(
                    bundle.component_hash("retrieval_policy") if bundle is not None else "default"
                ),
            },
        )
        if not result.success or not isinstance(result.data, list) or not result.data:
            return KnowledgeContextResult()

        valid_items = [
            item for item in result.data
            if isinstance(item, dict) and not item.get("fallback") and str(item.get("content") or "").strip()
        ]
        candidates = tuple(ContextCandidate(
            chunk_id=str(item.get("chunk_id") or f"legacy-{index}"),
            document_id=str(item.get("document_id") or item.get("chunk_id") or f"legacy-{index}"),
            text=str(item.get("content") or "").strip(),
            start_char=int(item.get("source_start_char") or 0),
            end_char=int(item.get("source_end_char") or len(str(item.get("content") or ""))),
            title=str(item.get("title") or ""),
            score=float(item.get("score") or 0.0),
            ranks=tuple(sorted(
                (str(key), int(value)) for key, value in (item.get("ranks") or {}).items()
            )),
            source_type=str(item.get("source_type") or ""),
            source_checksum=str(item.get("source_checksum") or ""),
            scope=str(item.get("scope") or "public"),
            scope_decision=str(item.get("scope_decision") or "allowed_public"),
            index_manifest_fingerprint=str(item.get("index_manifest_fingerprint") or ""),
        ) for index, item in enumerate(valid_items, 1))
        if not candidates:
            return KnowledgeContextResult()
        packed = _rag_context_packer.pack(
            candidates,
            max_tokens=int(policy["context_max_tokens"]),
            max_chunks=resolved_top_k,
            redundancy_threshold=1.0,
        )
        by_id = {item.chunk_id: item for item in candidates}
        selected = tuple(by_id[chunk_id] for chunk_id in packed.chunk_ids)
        if not selected:
            return KnowledgeContextResult(generation_status="packing_empty")
        evidence_pack = EvidencePack.from_packed(
            message,
            packed,
            retrieval_policy=policy,
            retrieval_trace=(
                valid_items[0].get("retrieval_trace")
                if valid_items and isinstance(valid_items[0].get("retrieval_trace"), dict)
                else {}
            ),
        )
        answer = None
        if _grounded_answer_generator is not None:
            answer = await _grounded_answer_generator.generate(
                message, selected, history=history,
            )
        parts = ["[知识库证据；chunk ID 可用于引用]"]
        item_by_chunk = {
            str(item.get("chunk_id") or f"legacy-{index}"): item
            for index, item in enumerate(valid_items, 1)
        }
        for index, candidate in enumerate(selected, 1):
            item = item_by_chunk[candidate.chunk_id]
            parts.append(
                f"{index}. [{candidate.chunk_id}] {str(item.get('title') or '未命名文档')}\n"
                f"   内容: {candidate.text}"
            )
        citations: tuple[str, ...] = ()
        claims: tuple[Dict[str, Any], ...] = ()
        conflicts: tuple[Dict[str, Any], ...] = ()
        grounded_answer = ""
        generation_status = "sources_only"
        if answer is not None:
            citations = answer.citations
            generation_status = "abstained" if answer.abstained else "grounded_draft"
            grounded_answer = answer.answer
            claims = tuple({
                "text": claim.text, "citations": list(claim.citations),
            } for claim in answer.claims)
            conflicts = tuple({
                "description": conflict.description,
                "citations": list(conflict.citations),
            } for conflict in answer.conflicts)
            if not answer.abstained:
                parts.append(
                    "[知识库有引用草稿；仅覆盖公共知识，不得覆盖业务工具的实时事实]\n"
                    f"{answer.answer}\n引用: {', '.join(answer.citations)}"
                )
        parts.append("资料不足时明确说明无法确认；订单、退款、账户当前状态必须调用业务工具核验。")
        return KnowledgeContextResult(
            text="\n".join(parts),
            used=True,
            citations=citations,
            generation_status=generation_status,
            answer=grounded_answer,
            claims=claims,
            conflicts=conflicts,
            abstained=bool(answer.abstained) if answer is not None else False,
            reason=str(getattr(answer, "reason", "") or "") if answer is not None else "",
            evidence_pack=evidence_pack,
        )
    except Exception as ex:
        logger.warning(f"构建知识库上下文失败: {ex}")
        return KnowledgeContextResult(generation_status=f"error:{type(ex).__name__}")


def _should_use_knowledge(message: str, intent=None) -> bool:
    """跳过纯寒暄，业务类问题才检索知识库，避免无关 RAG 干扰回复。"""
    msg = (message or "").strip().lower()
    if not msg:
        return False
    intent_value = getattr(intent, "value", intent)
    if intent_value in {"greeting", "feedback", "escalation", "human_handoff", "other"}:
        return False
    if intent_value in {
        "query", "request", "technical", "billing", "account", "complaint",
        "order_status", "logistics", "refund", "invoice", "payment_issue",
        "account_security", "technical_login", "technical_crash",
    }:
        return True
    greetings = {"你好", "您好", "嗨", "hi", "hello", "hey", "早上好", "晚上好"}
    if msg in greetings:
        return False
    business_keywords = [
        "退款", "订单", "物流", "配送", "发票", "扣款", "支付", "账单", "订阅",
        "登录", "报错", "错误", "崩溃", "会员", "积分", "账户", "密码", "地址",
        "refund", "order", "invoice", "payment", "error", "login",
    ]
    return len(msg) >= 4 or any(kw in msg for kw in business_keywords)


@app.get("/monitor")
async def monitor_summary(_principal: Principal = Depends(_admin_principal)):
    """实时监控摘要：Agent 成功率、工具统计、告警、优化建议。"""
    if _monitor is None:
        raise HTTPException(503, "服务未就绪")
    return _monitor.summary()


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(
    query: str,
    top_k: int = 5,
    _principal: Principal = Depends(_knowledge_principal),
):
    """
    演示检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    展示 MCP 工具调用的核心亮点。
    """
    if _tool_manager is None:
        raise HTTPException(503, "服务未就绪")
    policy = {}
    cache_scope = "default"
    if _bundle_registry is not None:
        active_bundle = await asyncio.to_thread(_bundle_registry.active)
        policy = dict(active_bundle.retrieval_policy)
        cache_scope = active_bundle.component_hash("retrieval_policy")
    result = await _tool_manager.search_with_rewrite(
        "knowledge_search",
        query,
        top_k=min(max(int(top_k), 1), int(policy.get("top_k", 5))),
        context={"retrieval_policy": policy, "cache_scope": _rag_cache_scope(cache_scope)},
    )
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """单篇文档输入。"""
    model_config = ConfigDict(extra="forbid")

    source_id: Optional[str] = Field(default=None, max_length=256)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    source_type: Literal["text", "markdown", "json"] = "text"
    checksum: Optional[str] = Field(default=None, min_length=64, max_length=64)
    scope: Literal["public"] = "public"


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    model_config = ConfigDict(extra="forbid")

    documents: List[DocInput] = Field(min_length=1, max_length=1000)


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    id: Optional[str] = None
    question: Optional[str] = None
    turns: Optional[List[str]] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None
    expected_agents: Optional[List[str]] = None
    expected_task_ids: Optional[List[str]] = None
    expected_disposition: Optional[Literal["execute", "clarify", "out_of_scope"]] = None
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    entities: Optional[Dict[str, List[str]]] = None
    rubric: Optional[Dict[str, Any]] = None


class EvalRunInput(BaseModel):
    """评测请求。可使用内置 smoke case、内联用例或注册数据集。"""
    intent_cases: Optional[List[EvalIntentInput]] = None
    dialog_cases: Optional[List[EvalDialogInput]] = None
    dataset_id: Optional[str] = None
    split: Literal["dev", "heldout"] = "dev"
    layers: Optional[List[Literal["intent", "routing", "retrieval", "stateful"]]] = None
    include_non_gold: bool = False
    bundle_version: Optional[str] = Field(default=None, min_length=1, max_length=128)


class EvalGraduationInput(BaseModel):
    """候选评测报告的显式晋级证据；普通 eval run 不会自动晋级。"""

    candidate_id: str = Field(min_length=1, max_length=160)
    hard_gates: Dict[str, bool]
    review_status: str = Field(min_length=1, max_length=80)
    fresh_heldout: bool
    heldout_evidence_id: str = Field(default="", max_length=240)
    heldout_checksum: str = Field(default="", max_length=64)
    latency_ratio: float = Field(default=1.0, ge=0.0)
    cost_ratio: float = Field(default=1.0, ge=0.0)


def _eval_dataset_root() -> pathlib.Path:
    """返回服务端控制的数据集注册表，客户端不能传入任意文件路径。"""
    configured = os.getenv("EVAL_DATASET_DIR", "").strip()
    return pathlib.Path(configured or pathlib.Path(_ROOT) / "data" / "eval")


def _registered_eval_inputs(body: EvalRunInput):
    """把版本化数据集样本转换为现有 evaluator 的运行合同。"""
    from evaluation.dataset import DatasetValidationError, load_registered_dataset
    from evaluation.evaluator import IntentTestCase

    if body.intent_cases is not None or body.dialog_cases is not None:
        raise HTTPException(
            422,
            detail={
                "code": "mixed_eval_sources",
                "message": "dataset_id 不能与内联 intent_cases/dialog_cases 同时使用",
            },
        )
    requested_layers = list(dict.fromkeys(body.layers or ["intent", "routing"]))
    if not requested_layers:
        raise HTTPException(422, detail={"code": "empty_eval_layers", "message": "layers 不能为空"})
    unsupported = sorted(set(requested_layers) - {"intent", "routing"})
    if unsupported:
        raise HTTPException(
            422,
            detail={
                "code": "runtime_layer_unsupported",
                "message": "retrieval/stateful 需通过 evaluation.benchmark 的预测文件评分",
                "layers": unsupported,
            },
        )
    try:
        bundle = load_registered_dataset(_eval_dataset_root(), body.dataset_id or "")
    except DatasetValidationError as exc:
        raise HTTPException(
            404,
            detail={"code": "eval_dataset_unavailable", "message": str(exc)},
        ) from exc

    selected = [
        case
        for case in bundle.select(split=body.split, gold_only=not body.include_non_gold)
        if case.layer in requested_layers
    ]
    if not selected:
        scope = "全部审核状态" if body.include_non_gold else "human_reviewed gold"
        raise HTTPException(
            409,
            detail={
                "code": "no_eligible_eval_cases",
                "message": f"数据集在 {body.split} / {scope} / {requested_layers} 下没有可运行样本",
            },
        )

    intent_cases = [
        IntentTestCase(
            message=str(case.input["message"]),
            expected_intent=str(case.expected["intent"]),
            context=case.input.get("context"),
        )
        for case in selected
        if case.layer == "intent"
    ]
    dialog_cases = [
        {
            "id": case.case_id,
            "question": str(case.input["message"]),
            "user_id": "eval_user",
            "expected_agents": list(map(str, case.expected["owners"])),
            "expected_task_ids": list(map(str, case.expected["task_ids"])),
            "expected_disposition": case.expected.get("disposition"),
            "evaluation_layer": "routing",
            "intent": case.input.get("intent"),
            "intent_confidence": case.input.get("intent_confidence"),
            "entities": case.input.get("entities") or {},
        }
        for case in selected
        if case.layer == "routing"
    ]
    metadata = {
        "source": "registered_dataset",
        "registry_id": body.dataset_id,
        "dataset_id": bundle.manifest.get("dataset_id"),
        "dataset_version": bundle.manifest.get("version"),
        "dataset_checksum": bundle.manifest.get("cases_sha256"),
        "split": body.split,
        "layers": requested_layers,
        "review_scope": "all" if body.include_non_gold else "human_reviewed",
        "case_count": len(selected),
    }
    return intent_cases, dialog_cases, metadata


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput, _principal: Principal = Depends(_admin_principal)):
    """
    批量导入文档到知识库。

    文档按配置的 Token 估算上限、结构边界和 overlap 切片后存入 ChromaDB。

    示例请求体：
    ```json
    {
      "documents": [
        {"source_id": "refund-policy", "title": "退款政策", "content": "用户在购买后 7 天内可以申请无理由退款..."},
        {"title": "配送说明", "content": "标准配送 3-5 个工作日..."}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    try:
        sources = [SourceDocument.create(
            source_id=d.source_id or "",
            title=d.title,
            content=d.content,
            source_type=d.source_type,
            checksum=d.checksum,
        ) for d in body.documents]
    except SourceDocumentContractError as exc:
        raise HTTPException(422, {"code": "source_document_invalid", "message": str(exc)}) from exc
    count = await kb.add_documents_async(sources)
    total = await kb.doc_count_async()
    return {
        "message": f"成功导入 {count} 个文档片段",
        "added_chunks": count,
        "total_chunks": total,
        "sources": [{
            "source_id": source.source_id,
            "source_type": source.source_type,
            "checksum": source.checksum,
            "scope": source.scope,
        } for source in sources],
    }


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(
    file: UploadFile = File(...),
    _principal: Principal = Depends(_admin_principal),
):
    """
    上传文件导入知识库。

    支持格式：
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, {"code": "invalid_utf8", "message": "文件必须是 UTF-8 编码"}) from exc
    filename = pathlib.PurePath(file.filename or "unknown").name
    suffix = pathlib.PurePath(filename).suffix.lower()
    if suffix not in {".txt", ".md", ".json"}:
        raise HTTPException(415, {
            "code": "unsupported_source_type",
            "message": "仅支持 .txt、.md、.json",
        })

    if suffix == ".json":
        import json as _json
        try:
            values = _json.loads(text)
            if not isinstance(values, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
            if not values:
                raise HTTPException(400, "JSON 文档数组不能为空")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        values = [{
            "title": pathlib.PurePath(filename).stem,
            "content": text,
            "source_type": "markdown" if suffix == ".md" else "text",
        }]

    try:
        sources = [
            SourceDocument.from_mapping(value, default_source_type="json")
            for value in values
        ]
    except SourceDocumentContractError as exc:
        raise HTTPException(422, {"code": "source_document_invalid", "message": str(exc)}) from exc
    count = await kb.add_documents_async(sources)
    total = await kb.doc_count_async()
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": total,
        "sources": [{
            "source_id": source.source_id,
            "source_type": source.source_type,
            "checksum": source.checksum,
            "scope": source.scope,
        } for source in sources],
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats(_principal: Principal = Depends(_admin_principal)):
    """查看知识库片段数、物理存储身份和实际索引/检索合同。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {
        "total_chunks": await kb.doc_count_async(),
        "storage_backend": kb.storage_backend,
        "index_manifest": kb.index_manifest,
        "active_bundle_retrieval_policy": (
            dict((await asyncio.to_thread(_bundle_registry.active)).retrieval_policy)
            if _bundle_registry is not None else {}
        ),
    }


@app.get("/eval/datasets")
async def list_eval_datasets(_principal: Principal = Depends(_admin_principal)):
    """列出服务端注册且通过合同校验的评测集及审核状态。"""
    from evaluation.dataset import discover_datasets

    return {"datasets": discover_datasets(_eval_dataset_root())}


@app.post("/eval/run")
async def run_eval(
    body: Optional[EvalRunInput] = None,
    _principal: Principal = Depends(_admin_principal),
):
    """运行内置/内联用例，或按版本、split、审核状态运行注册数据集。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    metadata: Dict[str, Any] = {"source": "builtin_smoke"}
    if body and body.dataset_id:
        intent_cases, dialog_cases, metadata = _registered_eval_inputs(body)
    elif body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
        dialog_cases = (
            [c.model_dump(exclude_none=True) for c in body.dialog_cases]
            if body.dialog_cases is not None
            else DEFAULT_DIALOG_CASES
        )
        metadata = {"source": "inline_or_mixed"}
    else:
        intent_cases = DEFAULT_INTENT_CASES
        if body and body.dialog_cases is not None:
            dialog_cases = [c.model_dump(exclude_none=True) for c in body.dialog_cases]
            metadata = {"source": "inline_or_mixed"}
        else:
            dialog_cases = DEFAULT_DIALOG_CASES

    metadata["model_policy"] = _model_policy.to_dict() if _model_policy is not None else None
    agent_bundle = None
    if body and body.bundle_version:
        if _bundle_registry is None:
            raise HTTPException(503, "Agent Bundle 服务未就绪")
        try:
            agent_bundle = await asyncio.to_thread(
                _bundle_registry.get, body.bundle_version,
            )
        except BundleNotFoundError as exc:
            raise HTTPException(404, {"code": "bundle_not_found"}) from exc
    elif _bundle_registry is not None:
        agent_bundle = await asyncio.to_thread(_bundle_registry.active)
    with capture_llm_usage() as usage:
        report = await _evaluator.run(
            intent_cases=intent_cases,
            dialog_cases=dialog_cases,
            metadata=metadata,
            agent_bundle=agent_bundle,
        )
    report.metadata["llm_usage"] = usage.summary()
    return {
        "pass_rate":       report.pass_rate,
        "total":           report.total,
        "passed":          report.passed,
        "avg_scores":      report.avg_scores,
        "regressions":     report.regressions,
        "recommendations": report.recommendations,
        "metadata":        report.metadata,
        "results": [
            {
                "test_id": r.test_id,
                "passed": r.passed,
                "scores": r.scores,
                "detail": r.detail,
                "metadata": r.metadata,
            }
            for r in report.results
        ],
    }


def _graduation_kwargs(body: EvalGraduationInput) -> Dict[str, Any]:
    """把 HTTP 证据投影为 Graduation Owner 的有限输入合同。"""
    return body.model_dump()


@app.get("/eval/baseline")
async def get_eval_baseline(_principal: Principal = Depends(_admin_principal)):
    """返回当前显式晋级的 Active Baseline；无基线时返回 null。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    snapshot = _evaluator.baseline_snapshot
    return {"active": snapshot.to_dict() if snapshot else None}


@app.post("/eval/graduation/check")
async def check_eval_graduation(
    body: EvalGraduationInput,
    _principal: Principal = Depends(_admin_principal),
):
    """检查最近一次评测是否满足晋级合同，不修改 Active 指针。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    try:
        decision = _evaluator.assess_latest_candidate(**_graduation_kwargs(body))
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "graduation_unavailable", "message": str(exc)}) from exc
    return {"decision": decision.to_dict(), "active_changed": False}


@app.post("/eval/baseline/promote")
async def promote_eval_baseline(
    body: EvalGraduationInput,
    principal: Principal = Depends(_admin_principal),
):
    """通过全部 Graduation Gate 后原子切换 Active Baseline。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    try:
        decision, snapshot = _evaluator.promote_latest_candidate(
            actor=principal.subject,
            **_graduation_kwargs(body),
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "graduation_unavailable", "message": str(exc)}) from exc
    return {
        "decision": decision.to_dict(),
        "active_changed": snapshot is not None,
        "active": snapshot.to_dict() if snapshot else None,
    }


@app.post("/evolution/rollouts/{version}/shadow", tags=["Agent Evolution"])
async def start_shadow_rollout(
    version: str,
    body: EvalGraduationInput,
    principal: Principal = Depends(_admin_principal),
):
    """用与候选绑定的 Graduation 证据启动 Shadow，不改变用户响应。"""
    if _rollout_manager is None or _evaluator is None:
        raise HTTPException(503, "Rollout 服务未就绪")
    if body.candidate_id != version:
        raise HTTPException(422, {"code": "candidate_version_mismatch"})
    try:
        decision = _evaluator.assess_latest_candidate(**_graduation_kwargs(body))
        status = await asyncio.to_thread(
            _rollout_manager.start_shadow,
            version,
            graduation=decision,
            actor=principal.subject,
        )
    except (ValueError, RolloutContractError, BundleNotFoundError) as exc:
        raise HTTPException(409, {"code": "shadow_start_rejected", "message": str(exc)}) from exc
    return {"rollout": status, "active_changed": False}


@app.post("/evolution/rollouts/{version}/canary", tags=["Agent Evolution"])
async def promote_canary_rollout(
    version: str,
    body: CanaryPromotionInput,
    principal: Principal = Depends(_admin_principal),
):
    if _rollout_manager is None:
        raise HTTPException(503, "Rollout 服务未就绪")
    try:
        status = await asyncio.to_thread(
            _rollout_manager.promote_canary,
            version,
            percent=body.percent,
            actor=principal.subject,
        )
    except RolloutContractError as exc:
        raise HTTPException(409, {"code": "canary_promotion_rejected", "message": str(exc)}) from exc
    return {"rollout": status, "active_changed": False}


@app.post("/evolution/rollouts/{version}/active", tags=["Agent Evolution"])
async def promote_active_rollout(
    version: str,
    principal: Principal = Depends(_admin_principal),
):
    if _rollout_manager is None:
        raise HTTPException(503, "Rollout 服务未就绪")
    try:
        status = await asyncio.to_thread(
            _rollout_manager.promote_active, version, actor=principal.subject,
        )
    except RolloutContractError as exc:
        raise HTTPException(409, {"code": "active_promotion_rejected", "message": str(exc)}) from exc
    return {"rollout": status, "active_changed": True}


@app.post("/evolution/rollouts/{version}/rollback", tags=["Agent Evolution"])
async def rollback_agent_bundle(
    version: str,
    body: RollbackInput,
    principal: Principal = Depends(_admin_principal),
):
    if _rollout_manager is None:
        raise HTTPException(503, "Rollout 服务未就绪")
    try:
        result = await asyncio.to_thread(
            _rollout_manager.rollback,
            version,
            actor=principal.subject,
            reason={"manual": body.reason},
        )
    except RolloutContractError as exc:
        raise HTTPException(409, {"code": "rollback_rejected", "message": str(exc)}) from exc
    return result


@app.post("/evolution/rollouts/signals/hard", tags=["Agent Evolution"])
async def submit_hard_rollout_signal(
    body: RolloutSignalInput,
    _principal: Principal = Depends(_admin_principal),
):
    """安全监控器可提交闭合集合内的硬信号并立即触发回滚。"""
    if _rollout_manager is None:
        raise HTTPException(503, "Rollout 服务未就绪")
    try:
        action = await asyncio.to_thread(
            _rollout_manager.record_outcome,
            bundle_version=body.bundle_version,
            stage="safety_monitor",
            verified=False,
            latency_ms=0.0,
            request_id=body.request_id,
            hard_signal=body.signal,
        )
    except (RolloutContractError, ValueError) as exc:
        raise HTTPException(409, {"code": "hard_signal_rejected", "message": str(exc)}) from exc
    return {"action": action}


@app.get("/evolution/rollouts/{version}", tags=["Agent Evolution"])
async def get_rollout_status(
    version: str,
    _principal: Principal = Depends(_admin_principal),
):
    if _rollout_manager is None:
        raise HTTPException(503, "Rollout 服务未就绪")
    try:
        return await asyncio.to_thread(_rollout_manager.status, version)
    except RolloutContractError as exc:
        raise HTTPException(404, {"code": "rollout_not_found"}) from exc


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    """提供无需启动 HTTP 服务的最小交互式调试入口。"""
    print(BANNER)
    print("DialogPilot CLI — 输入 quit 退出\n")

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from memory.conversation_memory import MemoryManager, MsgRole
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    skill_manager = SkillManager(
        root_dir=os.getenv("DIALOGPILOT_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills")),
        max_prompt_chars=int(os.getenv("DIALOGPILOT_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    skill_manager.load()
    orch = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=skill_manager,
        intent_similarity_mode=os.getenv("INTENT_SIMILARITY_MODE", "ngram"),
    )
    mem  = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        chroma_mode=os.getenv("CHROMA_MODE", "embedded"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())
    identity_factory = IdentityFactory()

    while True:
        try:
            msg = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 ʕ•ᴥ•ʔ")
            break
        if not msg or msg.lower() in ("quit", "exit", "退出"):
            print("再见 ʕ•ᴥ•ʔ")
            break

        ctx = await mem.get_context(user_id, conv_id, query=msg)
        history = [
            {"role": m.role.value, "content": m.content}
            for m in ctx.recent_messages[-5:]
        ] if ctx.recent_messages else None
        identity = identity_factory.create_invocation(
            tenant_id="cli",
            user_id=user_id,
            conversation_id=conv_id,
            request_id=None,
        )
        req = Request(
            message=msg,
            user_id=user_id,
            conv_id=conv_id,
            request_id=str(identity.request_id),
            identity_metadata=identity.metadata(),
            context=ctx.to_prompt_text(),
            history=history,
        )
        result = await orch.run(req)

        disposition = getattr(result.routing_disposition, "value", result.routing_disposition)
        if disposition != "out_of_scope":
            await mem.add_messages(user_id, conv_id, [
                (MsgRole.USER, msg, identity.metadata()),
                (MsgRole.ASSISTANT, result.response, identity.metadata()),
            ])

        responder = result.agent_type.value if result.agent_type else "orchestrator"
        print(f"\nDialogPilot [{responder}]: {result.response}\n")

    await mem.close()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
