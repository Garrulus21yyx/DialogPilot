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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Response, UploadFile, File, Query, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from services.ticket_service import (
    IdempotencyConflictError,
    InvalidTransitionError,
    TicketNotFoundError,
    TicketPriority,
    TicketStatus,
    TicketWebhookDispatcher,
)
from services.commitment_service import (
    CommitmentIdempotencyConflictError,
    CommitmentNotFoundError,
    CommitmentStatus,
    CommitmentVersionConflictError,
    InvalidCommitmentTransitionError,
)
from services.response_delivery import DeliveryStatus, ResponseNotFoundError
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
from memory.context import ContextAssembler
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.grounded_answer_generator import GroundedAnswerGenerator
from mcp.evidence_pack import EvidencePack
from application.knowledge_source import KnowledgeSourceContractError
from mcp.source_document import SourceDocument, SourceDocumentContractError
from core.tracing import TraceRecorder, current_trace_id, trace_scope
from core.input_security import PromptInjectionGuard
from core.auth import AuthenticationError, AuthorizationError, JWTAuthenticator, Principal
from core.llm_metrics import capture_llm_usage
from core.model_policy import ModelPolicy, ModelRole
from core.rag_policy import DEFAULT_RAG_RETRIEVAL_POLICY, rag_retrieval_policy_from_env, validate_rag_policy
from core.intent_recognizer import IntentCategory
from core.identity import IdentityFactory
from application.chat_contracts import ChatCommand, Completed
from api.ticket_resolution import (
    TicketResolutionAcceptRequest,
    accept_ticket_resolution_request,
)
from application.authority_policy import AuthorityPolicyRegistry
from application.hybrid_retrieval import RetrievalStatus
from application.knowledge_retriever import (
    EvidencePackResult,
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
    KnowledgeRetriever,
)
from application.public_chat_contract import project_chat_outcome
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
_memory       = None
_knowledge_store = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_skill_manager = None
_answer_verifier = None
_ticket_service = None
_commitment_service = None
_response_delivery = None
_badcase_registry = None
_customer_operations = None
_context_assembler = None
_rag_context_packer = ContextPacker()
_grounded_answer_generator = None
_knowledge_retriever = None
_retrieval_cache_client = None
_authenticator = None
_model_policy = None
_bundle_registry = None
_proposal_generator = None
_postgres_pool = None
_retrieval_postgres_pool = None
_service_episode_search = None
_conversation_query = None
_media_asset_store = None
_media_asset_service = None
_vlm_provider = None
_postgres_trace_sink = None
_target_run_coordinator = None
_durable_chat_task = None
_durable_chat_stop = None
_target_chat_runtime = None
_target_orchestration = None
_intent_recognizer = None
_target_checkpoint_owner = None
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
    global _memory, _knowledge_store, _tool_manager, _monitor, _evaluator, _skill_manager, _answer_verifier, _ticket_service, _commitment_service, _response_delivery, _badcase_registry, _customer_operations, _context_assembler, _authenticator, _model_policy, _bundle_registry, _proposal_generator, _grounded_answer_generator, _postgres_pool, _conversation_query, _knowledge_retriever, _retrieval_cache_client, _target_run_coordinator, _durable_chat_task, _durable_chat_stop, _retrieval_postgres_pool, _service_episode_search, _media_asset_store, _media_asset_service, _vlm_provider, _postgres_trace_sink, _target_chat_runtime, _target_checkpoint_owner

    global _target_orchestration, _intent_recognizer

    print(BANNER, flush=True)

    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from evaluation.chat_application_runner import ChatApplicationRunner
    from mcp.customer_support_tools import ticket_tools
    from mcp.commitment_tools import commitment_tools
    from mcp.customer_operations_tools import customer_operation_tools
    from mcp.tool_manager import ApprovalMode, MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from infrastructure.postgres_memory_fact_store import PostgresMemoryFactStore
    from monitor.performance_monitor import PerformanceMonitor
    from core.skill_loader import SkillManager
    from services.answer_verifier import AnswerVerifier
    from services.customer_operations import CustomerOperationsService

    cfg = _anthropic_cfg()
    _model_policy = cfg["policy"]
    from infrastructure.dense_embedding_factory import (
        KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
        DenseEmbeddingProviderFactory,
    )

    dense_embedding_factory = DenseEmbeddingProviderFactory(os.environ)
    vlm_enabled = os.getenv("VLM_ENABLED", "false").strip().lower()
    if vlm_enabled not in {"true", "false"}:
        raise RuntimeError("VLM_ENABLED must be true or false")
    if vlm_enabled == "true":
        from anthropic import Anthropic
        from infrastructure.deepseek_vision_provider import (
            DeepSeekVisionProvider,
        )

        vision_key = os.getenv("VLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY", "")
        vision_model = os.getenv(
            "MODEL_VISION", "deepseek-v4-flash-vision-exp",
        ).strip()
        vision_base_url = os.getenv(
            "VLM_BASE_URL", "https://api.deepseek.com/anthropic",
        ).strip()
        if not vision_key or not vision_model or not vision_base_url:
            raise RuntimeError("enabled DeepSeek vision configuration is incomplete")
        _vlm_provider = DeepSeekVisionProvider(
            Anthropic(api_key=vision_key, base_url=vision_base_url),
            model=vision_model,
            max_tokens=int(os.getenv("VLM_MAX_TOKENS", "512")),
        )
    similarity_mode = os.getenv("INTENT_SIMILARITY_MODE", "ngram")
    _authenticator = JWTAuthenticator.from_env()
    bootstrap_rag_policy = rag_retrieval_policy_from_env(os.environ)
    default_bundle = build_default_bundle(_model_policy.to_dict(), bootstrap_rag_policy)
    _proposal_generator = build_llm_proposal_generator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model_profile=_model_policy.profile(ModelRole.JUDGE),
    )
    logger.info("模型分层策略: %s", _model_policy.to_dict())

    # 旧标签仅用于意图评测和诊断；在线路由由 Target understanding 负责。
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        similarity_mode=similarity_mode,
        model_profile=_model_policy.profile(ModelRole.INTENT),
        cache_ttl_seconds=float(os.getenv("INTENT_CACHE_TTL_SECONDS", "3600")),
    )
    _intent_recognizer = recognizer

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("DIALOGPILOT_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("DIALOGPILOT_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()

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
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        from infrastructure.postgres import (
            PostgresMigrationRunner,
            PostgresPool,
            PostgresPoolConfig,
        )
        from infrastructure.postgres_conversation_query import (
            PostgresConversationQueryService,
        )

        PostgresMigrationRunner(database_url).upgrade()
        _postgres_pool = PostgresPool(PostgresPoolConfig.from_env())
        _postgres_pool.open()
        from infrastructure.postgres_trace_sink import PostgresTraceSink

        _postgres_trace_sink = PostgresTraceSink(
            _postgres_pool,
            retention_days=int(os.getenv("TRACE_RETENTION_DAYS", "7")),
        )
        trace_sinks = [_postgres_trace_sink]
        if os.getenv("LANGFUSE_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv(
                "LANGFUSE_SECRET_KEY"
            ):
                raise RuntimeError(
                    "LANGFUSE_ENABLED requires LANGFUSE_PUBLIC_KEY and "
                    "LANGFUSE_SECRET_KEY"
                )
            from infrastructure.langfuse_trace_sink import LangfuseTraceSink

            trace_sinks.append(LangfuseTraceSink())
        _trace_recorder.configure_sinks(trace_sinks)
        from infrastructure.postgres_ticket_service import PostgresTicketService

        _ticket_service = PostgresTicketService(
            _postgres_pool,
            dispatcher=ticket_dispatcher,
            dispatch_poll_seconds=float(os.getenv(
                "TICKET_DISPATCH_POLL_SECONDS", "5",
            )),
            dispatch_lease_seconds=float(os.getenv(
                "TICKET_DISPATCH_LEASE_SECONDS", "30",
            )),
            dispatch_retry_base_seconds=float(os.getenv(
                "TICKET_DISPATCH_RETRY_BASE_SECONDS", "5",
            )),
            dispatch_retry_max_seconds=float(os.getenv(
                "TICKET_DISPATCH_RETRY_MAX_SECONDS", "300",
            )),
        )
        from infrastructure.postgres_commitment_service import (
            PostgresCommitmentService,
        )

        _commitment_service = PostgresCommitmentService(
            _postgres_pool,
            due_poll_seconds=float(os.getenv("COMMITMENT_DUE_POLL_SECONDS", "30")),
        )
        from infrastructure.retrieval_runtime import build_retrieval_runtime

        retrieval_runtime = build_retrieval_runtime(
            _postgres_pool,
            database_url,
            os.environ,
            embedding_factory=dense_embedding_factory,
        )
        _retrieval_postgres_pool = retrieval_runtime.pool
        _retrieval_postgres_pool.open()
        _service_episode_search = retrieval_runtime.service_episode_search
        retrieval_projector = retrieval_runtime.projector
        _conversation_query = PostgresConversationQueryService(
            _postgres_pool,
            ticket_reader=_active_ticket_status_reader,
        )
        from infrastructure.postgres_response_delivery import (
            PostgresResponseDeliveryService,
        )

        _response_delivery = PostgresResponseDeliveryService(
            _postgres_pool,
            resume_binding_secret=(
                os.getenv("RESUME_BINDING_SECRET")
                or os.getenv("AUTH_JWT_SECRET", "")
            ),
        )
        from infrastructure.postgres_media_asset_store import (
            MediaAssetService,
            PostgresMediaAssetStore,
            local_signature_scan,
        )

        _media_asset_store = PostgresMediaAssetStore(_postgres_pool)
        _media_asset_service = MediaAssetService(
            _media_asset_store, local_signature_scan,
        )
    if _postgres_pool is None or _response_delivery is None:
        raise RuntimeError("DATABASE_URL is required for the PostgreSQL runtime")
    _bundle_registry = AgentBundleRegistry(_postgres_pool)
    _bundle_registry.bootstrap(default_bundle)
    _badcase_registry = BadCaseRegistry(
        _postgres_pool,
        identity_salt=os.getenv("BADCASE_IDENTITY_SALT") or os.getenv("AUTH_JWT_SECRET", ""),
    )
    _customer_operations = CustomerOperationsService(
        _postgres_pool, tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
    )
    _context_assembler = ContextAssembler(
        max_input_tokens=int(os.getenv("CONTEXT_INPUT_BUDGET", "12000")),
        reserved_output_tokens=int(os.getenv("CONTEXT_OUTPUT_RESERVE", "1536")),
    )

    # 记忆管理器（Redis 当前窗口 + PostgreSQL 用户事实）
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        fact_store=(
            PostgresMemoryFactStore(_postgres_pool)
            if _postgres_pool is not None else None
        ),
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

    # MCP 工具管理器 + PostgreSQL Knowledge 单一路径。
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        approval_mode=ApprovalMode(os.getenv("TOOL_APPROVAL_MODE", "default")),
        trace_recorder=_trace_recorder,
        max_output_chars=int(os.getenv("TOOL_OUTPUT_MAX_CHARS", "4000")),
        rewrite_model_profile=_model_policy.profile(ModelRole.REWRITE),
        rerank_model_profile=_model_policy.profile(ModelRole.RERANK),
    )
    _grounded_answer_generator = GroundedAnswerGenerator(
        _tool_manager.llm_client, _model_policy.profile(ModelRole.SYNTHESIS),
    )
    from infrastructure.knowledge_embedding import (
        LocalHashKnowledgeEmbeddingBaseline,
    )
    from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
    from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
    from infrastructure.retrieval_postgres import (
        PostgresRetrievalGenerationRegistry,
    )

    _knowledge_store = PostgresKnowledgeStore(
        _postgres_pool,
        tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
        chunk_strategy=os.getenv("RAG_CHUNK_STRATEGY", "structure_aware"),
        chunk_max_tokens=int(os.getenv("RAG_CHUNK_MAX_TOKENS", "512")),
        chunk_overlap_tokens=int(os.getenv("RAG_CHUNK_OVERLAP_TOKENS", "64")),
        embedding_provider=dense_embedding_factory.build(
            selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
            baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
        ),
    )
    await _knowledge_store.ensure_defaults_async()
    logger.info(
        "PostgreSQL 知识库已加载: %s 个文档片段",
        await _knowledge_store.doc_count_async(),
    )

    import redis
    from infrastructure.knowledge_retriever_adapters import (
        ToolManagerQueryTransformerAdapter,
        ToolManagerRerankerAdapter,
    )
    from infrastructure.postgres_knowledge_retriever import (
        PostgresKnowledgeCandidateSource,
        PostgresKnowledgeEvidenceValidator,
    )
    from infrastructure.retrieval_cache import RedisRetrievalCache

    _retrieval_cache_client = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=False,
    )
    knowledge_candidate_source = PostgresKnowledgeCandidateSource(
        backend=PostgresHybridBackend(_retrieval_postgres_pool),
        generations=PostgresRetrievalGenerationRegistry(_postgres_pool),
        pool=_retrieval_postgres_pool,
        embed_query=_knowledge_store.embed_query,
    )
    _knowledge_retriever = KnowledgeRetriever(
        candidate_source=knowledge_candidate_source,
        transformer=ToolManagerQueryTransformerAdapter(_tool_manager),
        reranker=ToolManagerRerankerAdapter(_tool_manager),
        packer=_rag_context_packer,
        cache=RedisRetrievalCache(_retrieval_cache_client),
        evidence_validator=PostgresKnowledgeEvidenceValidator(
            knowledge_candidate_source,
        ),
    )

    _tool_manager.register(Tool(
        name="knowledge_search",
        description=(
            "检索公共知识证据。query必须是已结合上下文消解的完整问题，保留否定、编号与已知条件，不猜测未知条件。返回证据而非答案。（"
            "BM25/Dense + metadata 路由的加权 RRF）"
        ),
        handler=_knowledge_tool_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "as_of": {"type": "string", "description": "用户明确要求的政策适用时点，带时区ISO-8601；省略查当前"},
                "applicable_region": {"type": "string", "description": "仅填写已确认的地区；未知则省略"},
                "applicable_channel": {"type": "string", "description": "仅填写已确认的销售渠道；未知则省略"},
                "applicable_product": {"type": "string", "description": "仅填写与来源标注一致的商品范围ID；未知则省略"},
                "top_k": {"type": "integer"},
                "source_types": {
                    "type": "array", "items": {"type": "string"},
                    "maxItems": 4,
                },
                "regions": {
                    "type": "array", "items": {"type": "string"},
                    "maxItems": 4,
                },
            },
            "required": ["query"],
        },
        cache_ttl=0.0,
        supports_rerank=False,
        fallback=None,
        allowed_agents=("general", "technical", "billing", "account_security"),
        read_only=True,
        authority="knowledge.active_source",
        manifest_version="tool-manifest-v1",
        output_schema_version="knowledge-evidence-pack-result-v1",
        preconditions=("active_source_manifest",),
        idempotency="read_only",
        retry_policy="safe_read_retry",
        typed_outcomes=("OK", "NO_EVIDENCE", "AMBIGUOUS", "UNAVAILABLE", "INVALID_CONTRACT", "CONFLICT"),
        output_fields=("status", "evidence_pack", "trace", "detail_code"),
    ))

    from application.service_episode_tool import build_service_episode_tool

    _tool_manager.register(build_service_episode_tool(
        lambda: _service_episode_search,
    ))
    for ticket_tool in ticket_tools(_ticket_service):
        _tool_manager.register(ticket_tool)
    for commitment_tool in commitment_tools(_commitment_service):
        _tool_manager.register(commitment_tool)
    for operation_tool in customer_operation_tools(_customer_operations):
        _tool_manager.register(operation_tool)
    if _media_asset_store is not None:
        from infrastructure.tesseract_ocr_provider import TesseractOCRProvider
        from mcp.product_tools import product_tools
        from services.product_catalog import ProductCatalogService

        product_catalog = ProductCatalogService(
            os.getenv(
                "PRODUCT_CATALOG_PATH",
                str(pathlib.Path(_ROOT) / "data" / "product-catalog.v1.json"),
            )
        )
        for product_tool in product_tools(
            _media_asset_store, TesseractOCRProvider(), product_catalog,
            vlm_provider=_vlm_provider,
        ):
            _tool_manager.register(product_tool)
    AuthorityPolicyRegistry.v1().validate_tools(_tool_manager.registered_tools)

    # Keep the HTTP lifespan as an adapter; Target wiring has its own cohesive
    # composition root and still reuses all existing runtime owners.
    from infrastructure.target_runtime_composition import build_target_runtime

    target_components = await build_target_runtime(
        database_url=database_url,
        postgres_pool=_postgres_pool,
        tool_manager=_tool_manager,
        memory=_memory,
        response_delivery=_response_delivery,
        model_policy=_model_policy,
        provider_config=cfg,
        project_root=pathlib.Path(_ROOT),
        knowledge_context_factory=_knowledge_execution_context,
        knowledge_generator=_grounded_answer_generator,
        knowledge_verifier=_answer_verifier,
        knowledge_source_validator=_knowledge_store.validate_publication_evidence,
    )
    _target_chat_runtime = target_components.application
    _target_orchestration = target_components.orchestration
    _target_run_coordinator = target_components.coordinator
    _target_checkpoint_owner = target_components.checkpoint_owner
    _conversation_query.runtime_reader = target_components.run_store.runtime

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        execution_runtime=_target_orchestration,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    from evaluation.target_planning_runner import TargetPlanningRunner

    _evaluator = EndToEndEvaluator(
        recognizer=recognizer,
        api_key=cfg["api_key"],
        tenant_id=target_components.registry.tenant_id,
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        judge_model_profile=_model_policy.profile(ModelRole.JUDGE),
        chat_runner=ChatApplicationRunner(
            lambda _overrides: _chat_application(),
            completion_reader=_target_run_coordinator.await_outcome,
        ),
        planning_runner_factory=lambda: TargetPlanningRunner(
            registry=target_components.registry,
            understanding=target_components.understanding,
            orchestration=target_components.orchestration,
        ),
    )

    await _memory.start()
    await _ticket_service.start()
    await _commitment_service.start()
    if _postgres_pool is not None:
        from infrastructure.memory_projection_adapter import (
            PostgresLegacyMemoryProjectionAdapter,
        )
        from infrastructure.embedded_thread_summarizer import (
            EmbeddedThreadSummarizerAdapter,
        )
        from infrastructure.postgres_thread_summary import (
            PostgresThreadSummaryRepository,
        )
        from application.thread_summary import (
            ThreadSummaryPolicy,
            ThreadSummaryProjector,
        )
        from infrastructure.postgres_projection import (
            ConversationProjectionDispatcher,
            PostgresConversationDeletionRepository,
            PostgresConversationProjectionOutbox,
        )
        from application.conversation_projection import ProjectionName
        from infrastructure.postgres_response_delivery import (
            PostgresResponseDeliveryService,
        )

        if not isinstance(
            _response_delivery, PostgresResponseDeliveryService,
        ):
            raise RuntimeError(
                "durable chat requires PostgreSQL response publication"
            )
        _durable_chat_stop = asyncio.Event()
        projection_adapters = {
            name: PostgresLegacyMemoryProjectionAdapter(
                _postgres_pool, _memory, name,
            )
            for name in ProjectionName if name is not ProjectionName.THREAD_SUMMARY
        }
        projection_adapters[ProjectionName.THREAD_SUMMARY] = ThreadSummaryProjector(
            PostgresThreadSummaryRepository(_postgres_pool),
            EmbeddedThreadSummarizerAdapter(
                _memory,
                version=f"embedded-{_model_policy.profile(ModelRole.MEMORY).model}-v1",
            ),
            policy=ThreadSummaryPolicy(
                message_threshold=int(os.getenv(
                    "THREAD_SUMMARY_MESSAGE_THRESHOLD", "12",
                )),
                token_threshold=int(os.getenv(
                    "THREAD_SUMMARY_TOKEN_THRESHOLD", "2000",
                )),
                max_events_per_job=int(os.getenv(
                    "THREAD_SUMMARY_MAX_EVENTS_PER_JOB", "50",
                )),
            ),
        )
        projection_dispatcher = ConversationProjectionDispatcher(
            outbox=PostgresConversationProjectionOutbox(_postgres_pool),
            deletion=PostgresConversationDeletionRepository(_postgres_pool),
            adapters=projection_adapters,
        )
        _durable_chat_task = asyncio.create_task(
            _run_durable_chat_worker(
                _target_run_coordinator,
                projection_dispatcher,
                retrieval_projector,
                _durable_chat_stop,
            ),
        )
    logger.info("DialogPilot 已就绪")
    try:
        yield
    finally:
        if _durable_chat_stop is not None:
            _durable_chat_stop.set()
        if _durable_chat_task is not None:
            await _durable_chat_task
        if _monitor is not None:
            await _monitor.stop()
        if _ticket_service is not None:
            await _ticket_service.close()
        if _commitment_service is not None:
            await _commitment_service.close()
        if _memory is not None:
            await _memory.close()
        if _retrieval_cache_client is not None:
            await asyncio.to_thread(_retrieval_cache_client.close)
        if _retrieval_postgres_pool is not None:
            _retrieval_postgres_pool.close()
        _trace_recorder.close()
        if _target_checkpoint_owner is not None:
            await _target_checkpoint_owner.__aexit__(None, None, None)
        if _postgres_pool is not None:
            _postgres_pool.close()
        # lifespan 结束后不留下指向已关闭资源的进程全局引用。
        _memory = None
        _knowledge_store = None
        _tool_manager = None
        _monitor = None
        _evaluator = None
        _skill_manager = None
        _answer_verifier = None
        _ticket_service = None
        _commitment_service = None
        _response_delivery = None
        _badcase_registry = None
        _customer_operations = None
        _context_assembler = None
        _authenticator = None
        _model_policy = None
        _bundle_registry = None
        _proposal_generator = None
        _retrieval_postgres_pool = None
        _service_episode_search = None
        _postgres_pool = None
        _conversation_query = None
        _media_asset_store = None
        _media_asset_service = None
        _vlm_provider = None
        _postgres_trace_sink = None
        _knowledge_retriever = None
        _retrieval_cache_client = None
        _target_run_coordinator = None
        _durable_chat_task = None
        _durable_chat_stop = None
        _target_chat_runtime = None
        _target_orchestration = None
        _intent_recognizer = None
        _target_checkpoint_owner = None
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


@app.get("/traces/{trace_id}", tags=["可观测性"])
async def get_persisted_trace(
    trace_id: str,
    _principal: Principal = Depends(_admin_principal),
):
    """Read sanitized local spans; raw prompts, tool outputs and secrets are absent."""
    if _postgres_trace_sink is None:
        raise HTTPException(503, "持久 Trace 服务未就绪")
    spans = await asyncio.to_thread(_postgres_trace_sink.get_trace, trace_id)
    if not spans:
        raise HTTPException(404, {"error": "trace_not_found"})
    return {"trace_id": trace_id, "spans": spans, "count": len(spans)}


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class InteractionValueInput(BaseModel):
    target_work_item_id: str = Field(min_length=1, max_length=200)
    field_name: str = Field(min_length=1, max_length=120)
    value: Any


class ChatRequest(BaseModel):
    """聊天入口的外部请求合同。"""
    message:     str = Field(min_length=1, max_length=10000)
    user_id:     Optional[str] = Field(default=None, min_length=1, max_length=200)
    conv_id:     Optional[str] = None
    request_id:  Optional[str] = Field(default=None, max_length=128)
    asset_ids: List[str] = Field(default_factory=list, max_length=5)
    approval_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    approved: Optional[bool] = None
    interaction_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    interaction_version: Optional[int] = Field(default=None, ge=1)
    interaction_values: List[InteractionValueInput] = Field(
        default_factory=list,
        max_length=20,
    )


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
    evaluation_trace: Dict[str, Any] = Field(default_factory=dict)
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
    awaiting_approval: bool = False
    react_run_ids: List[str] = Field(default_factory=list)
    pending_approval_call_ids: List[str] = Field(default_factory=list)
    pending_signals: List[Dict[str, Any]] = Field(default_factory=list)
    media: Dict[str, Any] = Field(default_factory=dict)


class AcceptedChatResponse(BaseModel):
    outcome: Literal["accepted"]
    workflow_run_id: str
    public_status: Dict[str, Any]
    client_action: Literal["poll_same_request"]
    retry_hint: Literal["reuse_request_id"]


class NeedsInputChatResponse(BaseModel):
    outcome: Literal["needs_input"]
    workflow_run_id: str
    signal_id: str
    kind: str
    expires_at: str
    interaction_publication_id: str
    client_action: Literal["reply_to_signal_schema"]
    retry_hint: Literal["do_not_create_new_request"]


class ReconcilingChatResponse(BaseModel):
    outcome: Literal["reconciling"]
    workflow_run_id: str
    public_status: Dict[str, Any]
    next_poll_after: float
    client_action: Literal["poll_only"]
    retry_hint: Literal["never_replay_write"]


class HandedOffChatResponse(BaseModel):
    outcome: Literal["handed_off"]
    ticket_id: str
    handoff_id: str
    client_action: Literal["continue_with_human_support"]
    retry_hint: Literal["do_not_restart_execution"]


class CancelledChatResponse(BaseModel):
    outcome: Literal["cancelled"]
    workflow_run_id: str
    reason_code: str
    client_action: Literal["start_new_request_if_needed"]
    retry_hint: Literal["do_not_retry_same_request"]


class ExpiredChatResponse(BaseModel):
    outcome: Literal["expired"]
    workflow_run_id: str
    stage: str
    new_request_required: bool
    client_action: Literal["start_new_request"]
    retry_hint: Literal["old_run_must_not_be_revived"]


class ConflictChatResponse(BaseModel):
    outcome: Literal["conflict"]
    code: str
    existing_invocation: Dict[str, Any]
    client_action: Literal["read_existing_or_change_request_id"]
    retry_hint: Literal["do_not_change_payload_for_same_key"]


class RejectedChatResponse(BaseModel):
    outcome: Literal["rejected"]
    code: str
    safe_message: str
    client_action: Literal["correct_request"]
    retry_hint: Literal["retry_only_after_correction"]


class FailedChatResponse(BaseModel):
    outcome: Literal["failed"]
    code: str
    retryable: bool
    correlation_id: str
    safe_message: str
    client_action: Literal["retry_same_request", "contact_support"]
    retry_hint: Literal["reuse_request_id", "do_not_blind_retry"]


ChatPublicResponse = (
    ChatResponse
    | AcceptedChatResponse
    | NeedsInputChatResponse
    | ReconcilingChatResponse
    | HandedOffChatResponse
    | CancelledChatResponse
    | ExpiredChatResponse
    | ConflictChatResponse
    | RejectedChatResponse
    | FailedChatResponse
)
ChatOkResponse = ChatResponse | HandedOffChatResponse | CancelledChatResponse
ChatAsyncResponse = (
    AcceptedChatResponse | NeedsInputChatResponse | ReconcilingChatResponse
)




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


class CommitmentCreateRequest(BaseModel):
    """仅供人工或已有业务动作 receipt 显式创建承诺。"""
    idempotency_key: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    ticket_id: Optional[str] = Field(default=None, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    due_at: datetime
    owner: str = Field(min_length=1, max_length=200)
    source_kind: Literal["manual", "business_action"]
    source_receipt_ref: Optional[str] = Field(default=None, max_length=500)
    retention_class: str = Field(default="support_standard", min_length=1, max_length=100)


class CommitmentTransitionRequest(BaseModel):
    status: CommitmentStatus
    expected_version: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=200)
    receipt_ref: Optional[str] = Field(default=None, max_length=500)
    note: str = Field(default="", max_length=1000)


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
    """Deprecated compatibility wait for projection watermark convergence."""
    conv_id: str
    summarized_messages: int
    finalized: bool
    already_empty: bool = False
    facts_flushed: bool = True
    target_event_seq: int = 0
    projection_watermarks: Dict[str, int] = Field(default_factory=dict)
    caught_up: bool = True
    deprecated: bool = True


class ConversationCloseRequest(BaseModel):
    reason_code: str = Field(default="user_closed", min_length=1, max_length=100)


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """汇总依赖就绪状态和运行时统计，不承担业务健康修复。"""
    if _target_orchestration is None or _intent_recognizer is None:
        raise HTTPException(503, "服务未就绪")
    storage = {
        "memory": _memory.storage_backend if _memory is not None else None,
        "knowledge": _knowledge_store.storage_backend if _knowledge_store is not None else None,
    }
    active_bundle = (
        await asyncio.to_thread(_bundle_registry.active)
        if _bundle_registry is not None else None
    )
    return {
        "status": "ok",
        "agents": _target_orchestration.get_stats(),
        "tools": _tool_manager.get_stats() if _tool_manager is not None else {},
        "storage": storage,
        "model_policy": _model_policy.to_dict() if _model_policy is not None else None,
        "input_security": _input_security_guard.get_stats(),
        "intent_recognizer": {
            **_intent_recognizer.cache_stats,
            "classifier_fingerprint": _intent_recognizer.classifier_fingerprint(active_bundle),
        },
        "badcases": _badcase_registry.stats() if _badcase_registry is not None else {},
        "memory_fact_jobs": _memory.fact_job_stats if _memory is not None else {},
        "response_delivery": _response_delivery.stats() if _response_delivery is not None else {},
        "ticket_outbox": _ticket_service.outbox_stats() if _ticket_service is not None else {},
        "observability": {
            "postgres_trace_persistence": _postgres_trace_sink is not None,
            "langfuse_enabled": os.getenv(
                "LANGFUSE_ENABLED", "false",
            ).strip().lower() in {"1", "true", "yes", "on"},
        },
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


async def _active_ticket_context(
    user_id: str,
    *,
    query: str = "",
    intent_or_topics: tuple[str, ...] = (),
    entity_refs: tuple[str, ...] = (),
):
    """把 TicketService 当前事项投影为 typed、task-relevant 上下文。"""
    from application.active_case import (
        ActiveCaseContextRenderer,
        ActiveCaseContextPolicy,
        ActiveCaseContextView,
        ActiveCaseState,
    )
    from infrastructure.active_case_projection import TicketServiceActiveCaseReader

    if _ticket_service is None:
        from application.active_case import ActiveCaseProjection
        projection = ActiveCaseProjection(
            ActiveCaseState.UNAVAILABLE,
            reason_codes=("TICKET_SERVICE_NOT_COMPOSED",),
        )
        return ActiveCaseContextView(
            projection, ActiveCaseContextPolicy().select(
                projection, query=query,
                intent_or_topics=intent_or_topics, entity_refs=entity_refs,
            ),
        )
    projection = await asyncio.to_thread(
        TicketServiceActiveCaseReader(_ticket_service).read,
        user_id=user_id, limit=20,
    )
    selection = ActiveCaseContextPolicy().select(
        projection, query=query,
        intent_or_topics=intent_or_topics, entity_refs=entity_refs,
    )
    if projection.state is not ActiveCaseState.CASES:
        return ActiveCaseContextView(projection, selection)
    section = ActiveCaseContextRenderer().router_section(selection)
    return ActiveCaseContextView(projection, selection, section)


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
    *,
    approval_pending: bool,
    route_mode: str,
) -> tuple[str, bool]:
    """Select by canonical RouteMode; audit observations never own candidate meaning."""
    knowledge_is_final = bool(
        route_mode == "knowledge_qa"
        and knowledge.answer
        and knowledge.generation_status in {"grounded_draft", "abstained"}
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
        "knowledge": _knowledge_store.storage_backend if _knowledge_store is not None else {},
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
    """只注册候选，不修改当前 Active Bundle。"""
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


async def _run_durable_chat_worker(
    coordinator, projection_dispatcher, retrieval_projector, stop: asyncio.Event,
) -> None:
    from application.conversation_projection import ProjectionName

    poll_seconds = max(
        0.05, float(os.getenv("DIALOGPILOT_DURABLE_CHAT_POLL_SECONDS", "1")),
    )
    lease_seconds = int(os.getenv("DIALOGPILOT_PROJECTION_LEASE_SECONDS", "30"))
    while not stop.is_set():
        try:
            work_count = await coordinator.pump_once()
            now = datetime.now(timezone.utc)
            for name in ProjectionName:
                results = await projection_dispatcher.dispatch_once_async(
                    projection_name=name,
                    worker_id=(
                        f"{os.getenv('DIALOGPILOT_DURABLE_CHAT_WORKER_ID', 'api-compat')}"
                        f"-projection-{name.value}"
                    ),
                    now=now.isoformat(),
                    lease_until=(now + timedelta(seconds=lease_seconds)).isoformat(),
                    retry_at=(now + timedelta(seconds=poll_seconds)).isoformat(),
                    limit=20,
                )
                work_count += len(results)
            retrieval_result = await asyncio.to_thread(
                retrieval_projector.project_next,
            )
            work_count += int(retrieval_result is not None)
        except Exception:
            logger.exception("durable run worker iteration failed")
            work_count = 0
        if work_count:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass




def _chat_application():
    """Return the durable Target /chat submission owner."""
    if _target_run_coordinator is None:
        raise RuntimeError("Target chat runtime is not ready")
    return _target_run_coordinator




def _active_ticket_status_reader(
    user_id: str, conversation_id: str,
) -> Mapping[str, Any] | None:
    if _ticket_service is None:
        return None
    tickets = _ticket_service.list_active_tickets(user_id=user_id, limit=20)
    ticket = next((item for item in tickets if item.conv_id == conversation_id), None)
    if ticket is None:
        return None
    return {
        "ticket_id": ticket.ticket_id,
        "status": ticket.status.value,
        "priority": ticket.priority.value,
        "assignee": ticket.assignee,
    }


@app.post(
    "/chat",
    response_model=ChatOkResponse,
    responses={
        202: {"model": ChatAsyncResponse},
        409: {"model": ConflictChatResponse},
        410: {"model": ExpiredChatResponse},
        422: {"model": RejectedChatResponse},
        500: {"model": FailedChatResponse},
        503: {"model": FailedChatResponse},
    },
)
async def chat(req: ChatRequest, principal: Principal = Depends(_chat_principal)):
    """Validate the HTTP request and map the protocol-neutral application outcome."""
    _enforce_user_input_security(req.message)
    command = ChatCommand(
        message=req.message,
        user_id=_subject_for_request(req.user_id, principal),
        tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
        conv_id=req.conv_id,
        request_id=req.request_id,
        authorization_fingerprint=_fingerprint({
            "subject": principal.subject,
            "scopes": sorted(principal.scopes),
        }),
        asset_ids=tuple(req.asset_ids),
        approval_id=req.approval_id,
        approval_decision=req.approved,
        interaction_id=req.interaction_id,
        interaction_version=req.interaction_version,
        interaction_values=tuple(
            (item.target_work_item_id, item.field_name, item.value)
            for item in req.interaction_values
        ),
    )
    outcome = await _chat_application().handle(command)
    if isinstance(outcome, Completed):
        return ChatResponse.model_validate(outcome.response)
    projection = project_chat_outcome(outcome)
    return JSONResponse(status_code=projection.status_code, content=projection.body)


@app.post("/assets/upload", status_code=201, tags=["多模态"])
async def upload_asset(
    conv_id: str = Query(min_length=1, max_length=200),
    request_id: str = Query(min_length=1, max_length=128),
    file: UploadFile = File(...),
    principal: Principal = Depends(_chat_principal),
):
    """Store and security-scan one turn-bound image/PDF without OCR or VLM."""
    if _media_asset_service is None:
        raise HTTPException(503, "附件服务未就绪")
    from application.media_asset import (
        AssetAdmissionPolicy,
        AssetStatus,
        MediaAssetError,
    )

    max_bytes = int(os.getenv("MEDIA_ASSET_MAX_BYTES", str(10 * 1024 * 1024)))
    content = await file.read(max_bytes + 1)
    identity = IdentityFactory().create_invocation(
        tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
        user_id=principal.subject,
        conversation_id=conv_id,
        request_id=request_id,
    )
    try:
        admission = AssetAdmissionPolicy(max_bytes=max_bytes).admit(
            tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
            turn_key=str(identity.turn_key), filename=file.filename or "attachment",
            declared_media_type=file.content_type or "", content=content,
        )
        stored = await asyncio.to_thread(
            _media_asset_service.admit_and_scan, admission, content,
        )
    except MediaAssetError as exc:
        status = 413 if exc.code == "ASSET_TOO_LARGE" else 415
        raise HTTPException(
            status, {"code": exc.code, "message": str(exc)},
        ) from exc
    if stored.status is AssetStatus.QUARANTINED:
        raise HTTPException(422, {
            "code": "ASSET_QUARANTINED",
            "message": "attachment failed the local security scan",
        })
    if stored.status is not AssetStatus.SCANNED:
        raise HTTPException(503, {
            "code": "ASSET_SCAN_FAILED",
            "message": "attachment scan is unavailable",
        })
    return {
        "asset_id": stored.asset_id,
        "modality": stored.modality.value,
        "media_type": stored.media_type,
        "byte_size": stored.byte_size,
        "checksum": stored.checksum,
        "status": stored.status.value,
        "turn_key": stored.turn_key,
        "ocr_invoked": False,
        "vlm_invoked": False,
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
    """Deprecated: wait for projections; this never closes the conversation."""
    if _conversation_query is not None:
        from infrastructure.postgres_conversation_query import (
            ConversationQueryAccessDenied,
            ConversationQueryNotFound,
        )

        try:
            progress = await asyncio.to_thread(
                _conversation_query.projection_progress,
                tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
                user_id=principal.subject,
                conversation_id=conv_id,
            )
        except (ConversationQueryNotFound, ConversationQueryAccessDenied) as exc:
            raise HTTPException(404, {"error": "conversation_not_found"}) from exc
        body = ConversationFinalizeResponse(
            conv_id=conv_id,
            summarized_messages=0,
            finalized=progress.caught_up,
            already_empty=progress.target_event_seq == 0,
            facts_flushed=(
                progress.watermarks.get("fact_extraction", 0)
                >= progress.target_event_seq
            ),
            target_event_seq=progress.target_event_seq,
            projection_watermarks=dict(progress.watermarks),
            caught_up=progress.caught_up,
        )
        if not progress.caught_up:
            return JSONResponse(status_code=202, content=body.model_dump())
        return body
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


@app.get("/conversations/{conv_id}/turns", tags=["会话"])
async def list_conversation_turns(
    conv_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(_chat_principal),
):
    """Return the complete public transcript with a stable turn cursor."""
    if _conversation_query is None:
        raise HTTPException(503, "会话查询服务未就绪")
    from infrastructure.postgres_conversation_query import (
        ConversationQueryAccessDenied,
        ConversationQueryNotFound,
    )

    try:
        turns = await asyncio.to_thread(
            _conversation_query.list_turns,
            tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
            user_id=principal.subject,
            conversation_id=conv_id,
            after_seq=after_seq,
            limit=limit,
        )
    except (ConversationQueryNotFound, ConversationQueryAccessDenied) as exc:
        raise HTTPException(404, {"error": "conversation_not_found"}) from exc
    return {
        "conv_id": conv_id,
        "turns": [turn.__dict__ for turn in turns],
        "last_seq": turns[-1].seq if turns else after_seq,
    }


@app.get("/conversations/{conv_id}/events", tags=["会话"])
async def stream_conversation_events(
    conv_id: str,
    request: FastAPIRequest,
    after_event_id: str | None = Query(default=None),
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
    principal: Principal = Depends(_chat_principal),
):
    """Replay committed public events, then follow the same durable cursor."""
    if _conversation_query is None:
        raise HTTPException(503, "会话查询服务未就绪")
    from application.conversation_events import reset_event
    from infrastructure.postgres_conversation_query import (
        ConversationQueryAccessDenied,
        ConversationQueryNotFound,
    )

    cursor = after_event_id or last_event_id or None
    scope = {
        "tenant_id": os.getenv("DEFAULT_TENANT_ID", "default"),
        "user_id": principal.subject,
        "conversation_id": conv_id,
    }
    try:
        first_page = await asyncio.to_thread(
            _conversation_query.list_public_events,
            **scope,
            after_event_id=cursor,
            limit=100,
        )
    except (ConversationQueryNotFound, ConversationQueryAccessDenied) as exc:
        raise HTTPException(404, {"error": "conversation_not_found"}) from exc

    async def event_stream():
        nonlocal cursor, first_page
        page = first_page
        poll_seconds = max(0.1, float(os.getenv("SSE_POLL_SECONDS", "1")))
        while True:
            if page.reset_required:
                yield reset_event(conv_id)
                return
            for event in page.events:
                cursor = event.event_id
                yield event.to_sse()
            if await request.is_disconnected():
                return
            if page.events:
                page = await asyncio.to_thread(
                    _conversation_query.list_public_events,
                    **scope,
                    after_event_id=cursor,
                    limit=100,
                )
                continue
            yield ": keep-alive\n\n"
            await asyncio.sleep(poll_seconds)
            page = await asyncio.to_thread(
                _conversation_query.list_public_events,
                **scope,
                after_event_id=cursor,
                limit=100,
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/invocations/{invocation_key}", tags=["会话"])
async def get_invocation_status(
    invocation_key: str,
    principal: Principal = Depends(_chat_principal),
):
    """Compose admission, execution, signal, delivery and ticket read models."""
    if _conversation_query is None:
        raise HTTPException(503, "会话查询服务未就绪")
    from infrastructure.postgres_conversation_query import (
        ConversationQueryAccessDenied,
        ConversationQueryNotFound,
    )

    try:
        view = await asyncio.to_thread(
            _conversation_query.invocation_status,
            invocation_key,
            tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
            user_id=principal.subject,
        )
    except (ConversationQueryNotFound, ConversationQueryAccessDenied) as exc:
        raise HTTPException(404, {"error": "invocation_not_found"}) from exc
    return {
        "invocation_key": view.invocation_key,
        "workflow_run_id": view.workflow_run_id,
        "admission_status": view.admission_status.value,
        "execution_status": (
            view.execution_status.value if view.execution_status else None
        ),
        "runtime": view.runtime,
        "pending_signal": view.pending_signal,
        "final_response": view.final_response,
        "delivery_status": (
            view.delivery_status.value if view.delivery_status else None
        ),
        "ticket": view.ticket,
    }


@app.post("/conversations/{conv_id}/close", tags=["会话"])
async def close_conversation(
    conv_id: str,
    body: ConversationCloseRequest,
    principal: Principal = Depends(_chat_principal),
):
    """Audit and close a conversation; unlike finalize, this fences later writes."""
    if _conversation_query is None:
        raise HTTPException(503, "会话查询服务未就绪")
    from infrastructure.postgres_conversation_query import (
        ConversationQueryAccessDenied,
        ConversationQueryNotFound,
    )

    try:
        result = await asyncio.to_thread(
            _conversation_query.close_conversation,
            tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
            user_id=principal.subject,
            conversation_id=conv_id,
            reason_code=body.reason_code,
            actor=principal.subject,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    except (ConversationQueryNotFound, ConversationQueryAccessDenied) as exc:
        raise HTTPException(404, {"error": "conversation_not_found"}) from exc
    return {
        "conv_id": conv_id,
        "close_id": result.close_id,
        "already_closed": result.already_closed,
        "closed_at": result.closed_at,
    }


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
    """Deprecated assistant-only compatibility projection; use /turns."""
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
        "projection_kind": "assistant_only_compatibility",
        "deprecated": True,
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


@app.post("/tickets/{ticket_id}/resolution", tags=["人工工单"])
async def accept_ticket_resolution(
    ticket_id: str,
    body: TicketResolutionAcceptRequest,
    principal: Principal = Depends(_admin_principal),
):
    """Accept a resolution as the authenticated, assigned case owner."""
    return await accept_ticket_resolution_request(
        _ticket_service,
        ticket_id,
        body,
        principal=principal,
    )


@app.post("/commitments", tags=["服务承诺"])
async def create_commitment(
    body: CommitmentCreateRequest,
    _principal: Principal = Depends(_admin_principal),
):
    """Create an explicit promise; generated assistant wording is not an input."""
    if _commitment_service is None:
        raise HTTPException(503, "承诺服务未就绪")
    try:
        item, created = await asyncio.to_thread(
            _commitment_service.create, **body.model_dump(),
        )
        return {"created": created, "commitment": item.to_dict()}
    except CommitmentIdempotencyConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/commitments", tags=["服务承诺"])
async def list_commitments(
    user_id: str,
    active_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    _principal: Principal = Depends(_admin_principal),
):
    if _commitment_service is None:
        raise HTTPException(503, "承诺服务未就绪")
    items = await asyncio.to_thread(
        _commitment_service.list_for_user,
        user_id=user_id, active_only=active_only, limit=limit,
    )
    return {"commitments": [item.to_dict() for item in items], "count": len(items)}


@app.get("/commitments/{commitment_id}", tags=["服务承诺"])
async def get_commitment(
    commitment_id: str,
    _principal: Principal = Depends(_admin_principal),
):
    if _commitment_service is None:
        raise HTTPException(503, "承诺服务未就绪")
    try:
        item = await asyncio.to_thread(_commitment_service.get, commitment_id)
        events = await asyncio.to_thread(_commitment_service.events, commitment_id)
        return {**item.to_dict(), "events": events}
    except CommitmentNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.patch("/commitments/{commitment_id}/status", tags=["服务承诺"])
async def update_commitment_status(
    commitment_id: str,
    body: CommitmentTransitionRequest,
    _principal: Principal = Depends(_admin_principal),
):
    if _commitment_service is None:
        raise HTTPException(503, "承诺服务未就绪")
    try:
        item = await asyncio.to_thread(
            _commitment_service.transition,
            commitment_id,
            body.status,
            expected_version=body.expected_version,
            actor=body.actor,
            receipt_ref=body.receipt_ref,
            note=body.note,
        )
        return item.to_dict()
    except CommitmentNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (CommitmentVersionConflictError, InvalidCommitmentTransitionError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


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
    """人工校正候选标签；校正结果仍需通过本地评测。"""
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


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _knowledge_execution_context() -> dict:
    """Snapshot host-owned policy and source identity once per durable turn."""
    bundle = _bundle_registry.active()
    generation = _knowledge_store.active_generation()
    return {
        "knowledge_as_of": datetime.now(timezone.utc).isoformat(),
        "retrieval_policy": validate_rag_policy(bundle.retrieval_policy),
        "cache_scope": bundle.version,
        "bundle_version": bundle.version,
        "pinned_execution_refs": {
            "bundle_version": bundle.version,
            "knowledge_backend_ref": generation.backend_fingerprint,
            "corpus_manifest_ref": generation.manifest_hash,
            "retrieval_policy_ref": bundle.version,
            "knowledge_generation_ref": generation.generation_id,
        },
    }


def _knowledge_policy(
    values: Mapping[str, Any], *, policy_version: str,
) -> KnowledgeRetrievalPolicy:
    from mcp.query_transformer import QUERY_TRANSFORM_PROMPT_VERSION
    from mcp.result_reranker import RERANK_PROMPT_VERSION

    policy = {**DEFAULT_RAG_RETRIEVAL_POLICY, **dict(values)}
    generation = _knowledge_store.active_generation()
    return KnowledgeRetrievalPolicy(
        policy_version=policy_version,
        backend_fingerprint=generation.backend_fingerprint,
        lexical_provider=generation.lexical_ranker,
        transformer_version=QUERY_TRANSFORM_PROMPT_VERSION,
        embedding_version=(
            generation.embedding_profile.fingerprint
        ),
        reranker_version=RERANK_PROMPT_VERSION,
        packer_version="context-packer-v1",
        raw_query_weight=float(policy["raw_query_weight"]),
        standalone_query_weight=float(policy["standalone_query_weight"]),
        expansion_query_weight=float(policy["expansion_query_weight"]),
        query_expansion_count=int(policy["query_expansion_count"]),
        metadata_hint_weight=float(policy["metadata_hint_weight"]),
        dense_weight=float(policy["vector_weight"]),
        lexical_weight=float(policy["lexical_weight"]),
        rrf_k=int(policy["rrf_k"]), candidate_k=int(policy["candidate_k"]),
        final_k=int(policy["top_k"]),
        context_max_tokens=int(policy["context_max_tokens"]),
    )


async def _subject_deletion_epoch(
    tenant_id: str, user_id: str, conversation_id: str,
) -> int:
    if not conversation_id:
        return 0
    if _postgres_pool is None:
        raise RuntimeError("conversation deletion fence is unavailable")
    def read():
        with _postgres_pool.transaction() as connection:
            return connection.execute("""
                SELECT deletion_epoch, deleted_at
                FROM dialogpilot_app.conversations
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, (tenant_id, user_id, conversation_id)).fetchone()

    row = await asyncio.to_thread(read)
    if row is None or row[1] is not None:
        raise RuntimeError("conversation is deletion-fenced")
    return int(row[0])


async def _retrieve_knowledge(
    query: str,
    *,
    history: tuple[str, ...],
    policy_values: Mapping[str, Any],
    policy_version: str,
    tenant_id: str,
    user_scope: str,
    conversation_id: str,
    authorization_fingerprint: str,
    requirement_signature: str,
    pinned_execution_refs: Any = None,
    bundle_version: str = "",
    source_type_hints: tuple[str, ...] = (),
    region_hints: tuple[str, ...] = (),
    query_mode: str = "HISTORY",
    as_of: datetime | None = None,
    applicable_region: str | None = None,
    applicable_channel: str | None = None,
    applicable_product: str | None = None,
) -> EvidencePackResult:
    if _knowledge_retriever is None or _knowledge_store is None:
        return EvidencePackResult(
            RetrievalStatus.UNAVAILABLE, None, None, "RETRIEVER_UNAVAILABLE",
        )
    if not all((tenant_id.strip(), user_scope.strip(), authorization_fingerprint)):
        return EvidencePackResult(
            RetrievalStatus.INVALID_CONTRACT, None, None,
            "SUBJECT_OR_AUTHORIZATION_MISSING",
        )
    try:
        epoch = await _subject_deletion_epoch(
            tenant_id, user_scope, conversation_id,
        )
    except RuntimeError:
        return EvidencePackResult(
            RetrievalStatus.CONFLICT, None, None, "SUBJECT_DELETION_FENCED",
        )
    generation = _knowledge_store.active_generation()
    manifest = generation.manifest_hash
    current_backend = generation.backend_fingerprint
    pinned = (
        dict(pinned_execution_refs)
        if isinstance(pinned_execution_refs, Mapping)
        else dict(getattr(pinned_execution_refs, "__dict__", {}) or {})
    )
    if pinned:
        if (
            str(pinned.get("bundle_version") or "") != bundle_version
            or str(pinned.get("knowledge_backend_ref") or "") != current_backend
            or str(pinned.get("corpus_manifest_ref") or "") != manifest
            or str(pinned.get("retrieval_policy_ref") or "") != policy_version
            or str(pinned.get("knowledge_generation_ref") or "")
            != generation.generation_id
        ):
            return EvidencePackResult(
                RetrievalStatus.CONFLICT, None, None,
                "PINNED_EXECUTION_REFS_UNAVAILABLE",
            )
        generation_id = str(pinned.get("knowledge_generation_ref") or "")
        if not generation_id:
            return EvidencePackResult(
                RetrievalStatus.INVALID_CONTRACT, None, None,
                "PINNED_GENERATION_MISSING",
            )
    else:
        generation_id = generation.generation_id
    request = KnowledgeRetrievalRequest(
        tenant_id=tenant_id, user_scope=user_scope,
        authorization_fingerprint=authorization_fingerprint,
        acl_policy_fingerprint="knowledge-public-acl-v1",
        deletion_epoch=epoch, requirement_signature=requirement_signature,
        query=query, history=history, query_mode=query_mode,
        conversation_range_hash=_fingerprint({"history": list(history)}),
        locale="zh-CN", product=None, manifest_fingerprint=manifest,
        generation_id=generation_id,
        policy=_knowledge_policy(policy_values, policy_version=policy_version),
        source_type_hints=source_type_hints,
        region_hints=region_hints, as_of=as_of or datetime.now(timezone.utc),
        applicable_region=applicable_region, applicable_channel=applicable_channel,
        applicable_product=applicable_product,
    )
    return await _knowledge_retriever.retrieve(request)


async def _knowledge_tool_handler(
    params: Dict[str, Any], context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Project an authenticated Agent tool invocation onto KnowledgeRetriever."""
    context = dict(context or {})
    try:
        from application.knowledge_tool_contract import knowledge_query_options
        options = knowledge_query_options({key: params[key] for key in (
            "as_of", "applicable_region", "applicable_channel", "applicable_product") if key in params})
        as_of_value = options.get("as_of") or context.get("knowledge_as_of")
        as_of = datetime.fromisoformat(as_of_value) if as_of_value else None
    except (ValueError, TypeError):
        return EvidencePackResult(RetrievalStatus.INVALID_CONTRACT, None, None, "INVALID_KNOWLEDGE_OPTIONS").to_dict(include_text=True)
    result = await _retrieve_knowledge(
        str(params.get("query") or ""),
        history=(), query_mode="RESOLVED", as_of=as_of,
        applicable_region=options.get("applicable_region"),
        applicable_channel=options.get("applicable_channel"),
        applicable_product=options.get("applicable_product"),
        policy_values=dict(context.get("retrieval_policy") or {}),
        policy_version=str(context.get("cache_scope") or "agent-bundle-unversioned"),
        tenant_id=str(context.get("tenant_id") or ""),
        user_scope=str(context.get("user_id") or ""),
        conversation_id=str(context.get("conv_id") or ""),
        authorization_fingerprint=str(
            context.get("authorization_fingerprint") or ""
        ),
        requirement_signature="knowledge.active_source",
        pinned_execution_refs=context.get("pinned_execution_refs"),
        bundle_version=str(context.get("bundle_version") or ""),
        source_type_hints=tuple(map(str, (
            params.get("source_types")
            or context.get("knowledge_source_types")
            or ()
        ))),
        region_hints=tuple(map(str, (
            params.get("regions") or context.get("knowledge_regions") or ()
        ))),
    )
    return result.to_dict(include_text=True)


async def _build_knowledge_context(
    message: str,
    intent=None,
    top_k: int = 5,
    bundle: Optional[AgentBundle] = None,
    history: List[str] | tuple[str, ...] = (),
    tenant_id: str = "",
    user_id: str = "",
    conversation_id: str = "",
    authorization_fingerprint: str = "",
    generate_answer: bool = True,
    pinned_execution_refs: Any = None,
    required_by_plan: bool = False,
) -> KnowledgeContextResult:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    唯一 KnowledgeRetriever 返回证据；本函数只构造 Grounded Answer 投影。
    """
    if _knowledge_retriever is None:
        return KnowledgeContextResult()
    if not required_by_plan and not _should_use_knowledge(message, intent=intent):
        return KnowledgeContextResult()
    try:
        policy = {
            **DEFAULT_RAG_RETRIEVAL_POLICY,
            **(dict(bundle.retrieval_policy) if bundle is not None else {}),
        }
        result = await _retrieve_knowledge(
            message, history=tuple(history[-8:]), policy_values=policy,
            policy_version=(
                bundle.component_hash("retrieval_policy")
                if bundle is not None else _fingerprint(policy)
            ),
            tenant_id=tenant_id, user_scope=user_id,
            conversation_id=conversation_id,
            authorization_fingerprint=authorization_fingerprint,
            requirement_signature="knowledge.active_source",
            pinned_execution_refs=pinned_execution_refs,
            bundle_version=str(getattr(bundle, "version", "") or ""),
        )
        if result.status is not RetrievalStatus.OK or result.evidence_pack is None:
            return KnowledgeContextResult(generation_status=result.status.value.lower())
        evidence_pack = result.evidence_pack
        selected = tuple(ContextCandidate(
            chunk_id=item.chunk_id, document_id=item.source_ref.source_id,
            text=item.text, start_char=item.source_ref.start_char,
            end_char=item.source_ref.end_char, title=item.title,
            score=item.score, ranks=item.source_ranks,
            source_type=item.source_ref.source_type,
            source_checksum=item.source_ref.checksum,
            source_revision=item.source_ref.source_revision,
            scope=item.source_ref.scope, scope_decision=item.scope_decision,
            index_manifest_fingerprint=evidence_pack.index_manifest_fingerprint,
        ) for item in evidence_pack.items)
        answer = None
        if generate_answer and _grounded_answer_generator is not None:
            answer = await _grounded_answer_generator.generate(
                message, selected, history=history,
            )
        parts = ["[知识库证据；chunk ID 可用于引用]"]
        item_by_chunk = {item.chunk_id: item for item in evidence_pack.items}
        for index, candidate in enumerate(selected, 1):
            item = item_by_chunk[candidate.chunk_id]
            parts.append(
                f"{index}. [{candidate.chunk_id}] {item.title or '未命名文档'}\n"
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
    if _knowledge_retriever is None:
        raise HTTPException(503, "服务未就绪")
    policy = {}
    cache_scope = "default"
    if _bundle_registry is not None:
        active_bundle = await asyncio.to_thread(_bundle_registry.active)
        policy = dict(active_bundle.retrieval_policy)
        cache_scope = active_bundle.component_hash("retrieval_policy")
    result = await _retrieve_knowledge(
        query, history=(), policy_values=policy, policy_version=cache_scope,
        tenant_id=os.getenv("DEFAULT_TENANT_ID", "default"),
        user_scope=_principal.subject, conversation_id="",
        authorization_fingerprint=_fingerprint({
            "subject": _principal.subject,
            "scopes": sorted(_principal.scopes),
        }),
        requirement_signature="knowledge.active_source",
    )
    body = result.to_dict(include_text=True)
    if result.evidence_pack is not None:
        limit = min(max(int(top_k), 1), len(result.evidence_pack.items))
        body["results"] = [
            item.to_dict(include_text=True)
            for item in result.evidence_pack.items[:limit]
        ]
    else:
        body["results"] = []
    return {"query": query, **body}


class DocInput(BaseModel):
    """单篇文档输入。"""
    model_config = ConfigDict(extra="forbid")

    source_id: Optional[str] = Field(default=None, max_length=256)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    source_type: Literal["text", "markdown", "json"] = "text"
    checksum: Optional[str] = Field(default=None, min_length=64, max_length=64)
    scope: Literal["public"] = "public"
    region: str = Field(default="global", max_length=128)
    product: str = Field(default="", max_length=128)
    channel: str = Field(default="global", max_length=128)
    effective_from: datetime | None = None
    effective_to: datetime | None = None


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

    文档按配置的 Token 估算上限、结构边界和 overlap 切片后写入 PostgreSQL。

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
    if _knowledge_store is None:
        raise HTTPException(503, "知识库未初始化")
    kb = _knowledge_store
    try:
        sources = [SourceDocument.create(
            source_id=d.source_id or "",
            title=d.title,
            content=d.content,
            source_type=d.source_type,
            checksum=d.checksum, region=d.region, product=d.product, channel=d.channel,
            effective_from=d.effective_from, effective_to=d.effective_to,
        ) for d in body.documents]
    except SourceDocumentContractError as exc:
        raise HTTPException(422, {"code": "source_document_invalid", "message": str(exc)}) from exc
    from core.cost_budget import OfflineIngestBudgetExceeded
    try:
        receipt = await kb.import_documents_async(sources)
        count = receipt.chunk_count
    except (SourceDocumentContractError, KnowledgeSourceContractError) as exc:
        raise HTTPException(422, {"code": "source_document_invalid", "message": str(exc)}) from exc
    except OfflineIngestBudgetExceeded as exc:
        raise HTTPException(429, {
            "code": exc.code, "dimension": exc.dimension,
            "observed": exc.observed, "limit": exc.limit,
            "policy_version": exc.policy_version,
        }) from exc
    total = await kb.doc_count_async()
    return {
        "message": f"成功导入 {count} 个文档片段",
        "added_chunks": count,
        "total_chunks": total,
        "sources": [{
            "source_id": source.source_id,
            "source_revision": source.revision_id,
            "source_type": source.source_type,
            "checksum": source.checksum,
            "scope": source.scope,
        } for source in receipt.revisions],
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
    if _knowledge_store is None:
        raise HTTPException(503, "知识库未初始化")
    kb = _knowledge_store

    content = await file.read()
    filename = pathlib.PurePath(file.filename or "unknown").name
    suffix = pathlib.PurePath(filename).suffix.lower()
    from application.cost_budget_policy import OFFLINE_KNOWLEDGE_INGEST_BUDGET
    from core.upload_security import TextUploadPolicy, UploadSecurityError
    try:
        TextUploadPolicy(
            max_bytes=OFFLINE_KNOWLEDGE_INGEST_BUDGET.max_source_bytes,
        ).validate(content, suffix=suffix)
    except UploadSecurityError as exc:
        status = 413 if exc.code == "upload_too_large" else 415
        raise HTTPException(
            status, {"code": exc.code, "message": str(exc)},
        ) from exc

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, {"code": "invalid_utf8", "message": "文件必须是 UTF-8 编码"}) from exc
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
            "scope": SourceDocument.PUBLIC_SCOPE,
        }]

    try:
        sources = [
            SourceDocument.from_mapping(value, default_source_type="json")
            for value in values
        ]
    except SourceDocumentContractError as exc:
        raise HTTPException(422, {"code": "source_document_invalid", "message": str(exc)}) from exc
    from core.cost_budget import OfflineIngestBudgetExceeded
    try:
        receipt = await kb.import_documents_async(sources)
        count = receipt.chunk_count
    except (SourceDocumentContractError, KnowledgeSourceContractError) as exc:
        raise HTTPException(422, {"code": "source_document_invalid", "message": str(exc)}) from exc
    except OfflineIngestBudgetExceeded as exc:
        raise HTTPException(429, {
            "code": exc.code, "dimension": exc.dimension,
            "observed": exc.observed, "limit": exc.limit,
            "policy_version": exc.policy_version,
        }) from exc
    total = await kb.doc_count_async()
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": total,
        "sources": [{
            "source_id": source.source_id,
            "source_revision": source.revision_id,
            "source_type": source.source_type,
            "checksum": source.checksum,
            "scope": source.scope,
        } for source in receipt.revisions],
    }


class KnowledgeWithdrawalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=1000)


@app.post("/knowledge/withdraw", tags=["知识库"])
async def withdraw_knowledge(body: KnowledgeWithdrawalInput, _principal: Principal = Depends(_admin_principal)):
    if _knowledge_store is None:
        raise HTTPException(503, "知识库未初始化")
    try:
        await asyncio.to_thread(_knowledge_store.withdraw_revision, body.source_id, body.revision_id, reason=body.reason)
    except KnowledgeSourceContractError as exc:
        raise HTTPException(422, {"code": "source_withdrawal_invalid", "message": str(exc)}) from exc
    return {"status": "WITHDRAWN", "source_id": body.source_id, "revision_id": body.revision_id}


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats(_principal: Principal = Depends(_admin_principal)):
    """查看知识库片段数、物理存储身份和实际索引/检索合同。"""
    if _knowledge_store is None:
        raise HTTPException(503, "知识库未初始化")
    kb = _knowledge_store
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


if __name__ == "__main__":
    if "--cli" in sys.argv:
        from api.cli import run_cli

        asyncio.run(run_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
