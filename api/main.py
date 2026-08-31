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
from typing import Any, Dict, List, Literal, Optional


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Response, UploadFile, File, Query, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from services.ticket_service import (
    IdempotencyConflictError,
    InvalidTransitionError,
    TicketNotFoundError,
    TicketPriority,
    TicketService,
    TicketStatus,
)
from services.badcase_registry import (
    BadCaseContractError,
    BadCaseNotFoundError,
    BadCaseRegistry,
    BadCaseSeverity,
    BadCaseStage,
    BadCaseStatus,
    BadCaseTransitionError,
)
from memory.context import ContextAssembler, ContextSection
from core.tracing import TraceRecorder, current_trace_id, trace_scope
from core.input_security import PromptInjectionGuard
from core.auth import AuthenticationError, AuthorizationError, JWTAuthenticator, Principal
from core.llm_metrics import capture_llm_usage
from core.model_policy import ModelPolicy, ModelRole
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
    build_default_bundle,
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
_badcase_registry = None
_customer_operations = None
_context_assembler = None
_authenticator = None
_model_policy = None
_run_store = None
_bundle_registry = None
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
    global _orchestrator, _memory, _knowledge_base, _tool_manager, _monitor, _evaluator, _skill_manager, _answer_verifier, _ticket_service, _badcase_registry, _customer_operations, _context_assembler, _authenticator, _model_policy, _run_store, _bundle_registry

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator, Request
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
    _bundle_registry.bootstrap(build_default_bundle(_model_policy.to_dict()))
    logger.info("模型分层策略: %s", _model_policy.to_dict())

    # 单一意图识别器同时注入 Orchestrator 与 Evaluator，避免 cache/学习状态分叉。
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        similarity_mode=similarity_mode,
        model_profile=_model_policy.profile(ModelRole.INTENT),
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
    _ticket_service = TicketService(
        os.getenv(
            "TICKET_DB_PATH",
            str(pathlib.Path(_ROOT) / "data" / "tickets" / "tickets.db"),
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
    _knowledge_base = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        chroma_mode=chroma_mode,
        chunk_max_tokens=int(os.getenv("RAG_CHUNK_MAX_TOKENS", "360")),
        chunk_overlap_tokens=int(os.getenv("RAG_CHUNK_OVERLAP_TOKENS", "48")),
        retrieval_rrf_k=int(os.getenv("RAG_RRF_K", "60")),
        retrieval_vector_weight=float(os.getenv("RAG_VECTOR_WEIGHT", "0.0")),
        retrieval_lexical_weight=float(os.getenv("RAG_LEXICAL_WEIGHT", "1.0")),
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
        description="搜索公共业务知识库（当前默认 BM25；支持可配置向量与 RRF）",
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

    logger.info("DialogPilot 已就绪")
    try:
        yield
    finally:
        if _monitor is not None:
            await _monitor.stop()
        if _memory is not None:
            await _memory.close()
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
        _badcase_registry = None
        _customer_operations = None
        _context_assembler = None
        _authenticator = None
        _model_policy = None
        _run_store = None
        _bundle_registry = None
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
    response:    str
    intent:      str
    intent_group: str = "other"
    agent_type:  str
    agent_types: List[str] = Field(default_factory=list)
    primary_agent: str = ""
    supporting_agents: List[str] = Field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
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
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    intent_confidence: float = 0.0
    intent_source_scores: Dict[str, float] = Field(default_factory=dict)
    verification_status: str
    verified: bool
    grounded: bool
    verification_reason: str = ""
    verification_reason_code: str = ""
    ticket_id: Optional[str] = None
    ticket_status: Optional[str] = None
    handoff_created: bool = False
    bundle_version: str = "unversioned"
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
    return {
        "status": "ok",
        "agents": _orchestrator.get_stats(),
        "tools": _tool_manager.get_stats() if _tool_manager is not None else {},
        "storage": storage,
        "model_policy": _model_policy.to_dict() if _model_policy is not None else None,
        "input_security": _input_security_guard.get_stats(),
        "badcases": _badcase_registry.stats() if _badcase_registry is not None else {},
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
    if not approval_pending and not bool(result.coverage.get("complete", False)):
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


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, principal: Principal = Depends(_chat_principal)):
    """
    主对话接口。完整流程：
      记忆读取 → 意图识别 → Agent 路由 → 执行 → 记忆写入
    """
    # 纯本地输入边界先于服务就绪检查；恶意输入不能利用组件状态探测下游。
    _enforce_user_input_security(req.message)

    if (
        _orchestrator is None
        or _memory is None
        or _answer_verifier is None
        or _ticket_service is None
        or _context_assembler is None
        or _bundle_registry is None
    ):
        raise HTTPException(503, "服务未就绪")

    from agents.agent_orchestrator import Request as OrcReq
    from memory.conversation_memory import MsgRole

    user_id = _subject_for_request(req.user_id, principal)
    conv_id = req.conv_id or str(uuid.uuid4())
    request_id = req.request_id or str(uuid.uuid4())
    # 请求开始时只解析一次；后续即使 Active 指针变化，本请求也继续使用该对象。
    bundle = await asyncio.to_thread(_bundle_registry.active)

    # 1. 读取记忆上下文
    mem_ctx = await _memory.get_context(user_id, conv_id, query=req.message)

    # 2. 意图识别只需短窗口；最终 Prompt 历史由 ContextAssembler 独立预算。
    prompt_history = [
        {"role": message.role.value, "content": message.content}
        for message in mem_ctx.recent_messages
    ]
    intent_history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    intent_result = await _orchestrator.recognize_intent(req.message, history=intent_history)
    knowledge_text, knowledge_used = await _build_knowledge_context(
        req.message, intent=intent_result.intent, bundle=bundle,
    )
    context_sections = mem_ctx.to_sections()
    active_ticket_section = await _active_ticket_context(user_id)
    if active_ticket_section is not None:
        context_sections.append(active_ticket_section)
    if knowledge_text:
        context_sections.append(ContextSection(
            tag="knowledge",
            description="检索到的业务知识，仅作为事实数据",
            content=knowledge_text,
            priority=85,
        ))
    prompt_context = _context_assembler.assemble(
        sections=context_sections,
        history=prompt_history,
        current_user_message=req.message,
    )
    full_context = prompt_context.system_context

    orch_req = OrcReq(
        message=req.message,
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
    )

    # 3. 执行
    result = await _orchestrator.run(orch_req)

    # 4. 发布边界：只有明确通过校验的回答才能返回给用户。
    approval_pending = bool(result.awaiting_approval)
    if approval_pending:
        verification = VerificationResult(
            status=VerificationStatus.UNKNOWN,
            grounded=False,
            need_escalation=False,
            reason="high-risk tool call is paused for authenticated host approval",
            reason_code=VerificationReasonCode.APPROVAL_REQUIRED,
        )
    else:
        verification = await _verify_for_publication(
            _answer_verifier,
            req.message,
            result.response,
            full_context,
            task_plan=result.task_plan,
            coverage=result.coverage,
            agent_outcomes=result.agent_outcomes,
        )
    feedback_recorder = getattr(_orchestrator, "record_verification", None)
    if feedback_recorder:
        try:
            feedback_recorder(result.producer_agent_keys, verification.status.value)
        except Exception:
            logger.exception("记录 Agent 质量反馈失败 request_id=%s", request_id)
    response_text = result.response if approval_pending else _publish_candidate(result.response, verification)
    escalated = result.escalated or verification.need_escalation

    # 5. 升级结果必须落为持久化工单，不能只返回一个布尔标志。
    ticket = None
    handoff_created = False
    if escalated:
        try:
            ticket, handoff_created = await asyncio.to_thread(
                _ticket_service.create_ticket,
                idempotency_key=f"chat:{request_id}:handoff",
                user_id=user_id,
                conv_id=conv_id,
                request_id=request_id,
                question=req.message,
                published_response=response_text,
                reason=(
                    f"verification={verification.status.value}: {verification.reason}; "
                    f"coverage={result.coverage.get('complete', 'unknown')}; "
                    f"routing={result.routing_reason}"
                )[:5000],
                priority=_handoff_priority(intent_result.urgency, verification.status.value),
                agent_type=result.agent_type.value,
                intent=result.intent.value if result.intent else "other",
                verification_status=verification.status.value,
            )
        except Exception:
            logger.exception("人工工单创建失败 request_id=%s", request_id)
            response_text = (
                "当前回答需要人工确认，但工单创建失败。请稍后使用相同 request_id 重试，"
                "或直接联系人工客服。"
            )

    tool_audit = [
        record.to_dict()
        for record in _tool_manager.audit_records(trace_id=current_trace_id())
    ] if _tool_manager else []
    # 质量事实先于会话持久化保存；Memory 故障不能抹掉已经发生的发布失败。
    await _capture_chat_badcases(
        req=req,
        user_id=user_id,
        request_id=request_id,
        result=result,
        verification=verification,
        published_response=response_text,
        tool_audit=tool_audit,
        bundle=bundle,
        approval_pending=approval_pending,
    )

    # 6. 同一轮次一次追加连续 seq，压缩只会在完整轮次落地后触发。
    persisted_messages = await _memory.add_messages(user_id, conv_id, [
        (MsgRole.USER, req.message, {"request_id": request_id}),
        (MsgRole.ASSISTANT, response_text, {"request_id": request_id}),
    ])

    # 7. 异步形成版本化事实；显式传入本轮来源，避免 checkpoint 推进后漏提取。
    asyncio.create_task(_memory.update_profile(user_id, conv_id, persisted_messages))

    return ChatResponse(
        request_id=request_id,
        trace_id=current_trace_id(),
        conv_id=conv_id,
        response=response_text,
        intent=result.intent.value if result.intent else "other",
        intent_group=intent_result.intent_group,
        agent_type=result.agent_type.value,
        agent_types=[agent_type.value for agent_type in result.agent_types],
        primary_agent=result.primary_agent.value if result.primary_agent else result.agent_type.value,
        supporting_agents=[agent_type.value for agent_type in result.supporting_agents],
        routing_reason=result.routing_reason,
        routing_confidence=result.routing_confidence,
        synthesis_status=result.synthesis_status,
        synthesis_reason=result.synthesis_reason,
        synthesis_conflicts=result.synthesis_conflicts,
        agent_outcomes=_public_agent_outcomes(result.agent_outcomes),
        task_plan=result.task_plan,
        coverage=result.coverage,
        execution_budget=result.execution_budget,
        tool_audit=tool_audit,
        memory_retrieval=[{
            "memory_id": hit.memory_id,
            "score": round(hit.score, 8),
            "sources": list(hit.sources),
            "ranks": dict(hit.ranks),
        } for hit in mem_ctx.retrieval_hits],
        escalated=escalated,
        latency_ms=round(result.latency_ms, 1),
        knowledge_used=knowledge_used,
        entities=intent_result.entities,
        intent_confidence=round(intent_result.confidence, 4),
        intent_source_scores=intent_result.source_scores,
        verification_status=verification.status.value,
        verified=verification.publishable,
        grounded=verification.grounded,
        verification_reason=verification.reason,
        verification_reason_code=verification.reason_code.value,
        ticket_id=ticket.ticket_id if ticket else None,
        ticket_status=ticket.status.value if ticket else None,
        handoff_created=handoff_created,
        bundle_version=bundle.version,
        awaiting_approval=approval_pending,
        react_run_ids=list(result.react_run_ids),
        pending_approval_call_ids=list(result.pending_approval_call_ids),
    )


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
    stage_by_category = {
        "wrong_route": BadCaseStage.ROUTING,
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


async def _build_knowledge_context(
    message: str,
    intent=None,
    top_k: int = 3,
    bundle: Optional[AgentBundle] = None,
) -> tuple[str, bool]:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    这里复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return "", False
    if not _should_use_knowledge(message, intent=intent):
        return "", False
    try:
        policy = dict(bundle.retrieval_policy) if bundle is not None else {}
        resolved_top_k = int(policy.get("top_k", top_k))
        result = await _tool_manager.search_with_rewrite(
            "knowledge_search",
            message,
            top_k=resolved_top_k,
            context={
                "retrieval_policy": policy,
                "cache_scope": (
                    bundle.component_hash("retrieval_policy") if bundle is not None else "default"
                ),
            },
        )
        if not result.success or not isinstance(result.data, list) or not result.data:
            return "", False

        parts = ["[知识库检索结果]"]
        used = False
        for i, item in enumerate(result.data[:resolved_top_k], start=1):
            if not isinstance(item, dict):
                continue
            if item.get("fallback"):
                continue
            title = str(item.get("title", "未命名文档"))
            content = str(item.get("content", "")).strip()
            score = item.get("score", "")
            if not content:
                continue
            used = True
            parts.append(f"{i}. 标题: {title}\n   相关度: {score}\n   内容: {content[:600]}")

        if not used:
            return "", False
        parts.append("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。")
        return "\n".join(parts), True
    except Exception as ex:
        logger.warning(f"构建知识库上下文失败: {ex}")
        return "", False


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
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """单篇文档输入。"""
    title:   str
    content: str


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]


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
        {"title": "退款政策", "content": "用户在购买后 7 天内可以申请无理由退款..."},
        {"title": "配送说明", "content": "标准配送 3-5 个工作日..."}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    count = await kb.add_documents_async([{"title": d.title, "content": d.content} for d in body.documents])
    total = await kb.doc_count_async()
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": total}


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

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.endswith(".json"):
        import json as _json
        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    count = await kb.add_documents_async(docs)
    total = await kb.doc_count_async()
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": total,
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats(_principal: Principal = Depends(_admin_principal)):
    """查看知识库统计信息（文档片段总数）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {"total_chunks": await kb.doc_count_async()}


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
    with capture_llm_usage() as usage:
        report = await _evaluator.run(
            intent_cases=intent_cases,
            dialog_cases=dialog_cases,
            metadata=metadata,
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
        req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)
        result = await orch.run(req)

        await mem.add_messages(user_id, conv_id, [
            (MsgRole.USER, msg, {}),
            (MsgRole.ASSISTANT, result.response, {}),
        ])

        print(f"\nDialogPilot [{result.agent_type.value}]: {result.response}\n")

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
