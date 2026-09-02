import asyncio
import os
from types import SimpleNamespace

from api import main


def test_lifespan_wires_memory_budget_to_memory_owner(tmp_path, monkeypatch):
    """证明 Memory 配置只传给 MemoryManager，不会误传给意图识别器。"""
    """The API boundary must send memory policy to MemoryManager, not intent."""
    import agents.agent_orchestrator as agent_module
    import core.intent_recognizer as intent_module
    import core.skill_loader as skill_module
    import evaluation.evaluator as evaluation_module
    import mcp.knowledge_base as knowledge_module
    import mcp.tool_manager as tool_module
    import memory.conversation_memory as memory_module
    import monitor.performance_monitor as monitor_module
    import services.answer_verifier as verifier_module

    captured = {}

    class FakeIntentRecognizer:
        def __init__(
            self,
            api_key,
            base_url=None,
            model=None,
            similarity_mode=None,
            model_profile=None,
            cache_ttl_seconds=None,
        ):
            captured["intent"] = {
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "similarity_mode": similarity_mode,
                "model_profile": model_profile,
                "cache_ttl_seconds": cache_ttl_seconds,
            }

    class FakeSkillManager:
        def __init__(self, **_kwargs):
            pass

        def load(self):
            return []

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            captured["orchestrator"] = kwargs

        def set_tool_manager(self, _tool_manager):
            captured["tool_manager_wired"] = True

        def get_stats(self):
            return {}

        def update_routing_penalties(self, _penalties):
            pass

    class FakeAnswerVerifier:
        def __init__(self, **_kwargs):
            pass

    class FakeMemoryManager:
        def __init__(self, **kwargs):
            captured["memory"] = kwargs
            captured["memory_closed"] = False

        async def start(self):
            captured["memory_started"] = True

        async def close(self):
            captured["memory_closed"] = True

    class FakeToolManager:
        def __init__(self, **kwargs):
            captured["tool_manager"] = kwargs
            self.tools = []
            self.llm_client = object()
            self._query_transformer = SimpleNamespace(standalone=None)
            self._result_reranker = SimpleNamespace(rerank=None)

        def register(self, tool):
            self.tools.append(tool)
            captured.setdefault("registered_tool_names", []).append(tool.name)

        @property
        def registered_tools(self):
            return tuple(self.tools)

        def get_stats(self):
            return {}

    class FakeKnowledgeBase:
        def __init__(self, **kwargs):
            captured["knowledge"] = kwargs

        async def doc_count_async(self):
            return 0

        @property
        def index_manifest(self):
            return {"manifest_fingerprint": "a" * 64}

        def validate_cached_candidates(self, _candidates):
            return True

        async def search_variants_async(self, *_args, **_kwargs):
            return []

    class FakeMonitor:
        def __init__(self, **_kwargs):
            captured["monitor_stopped"] = False

        async def start(self):
            pass

        async def stop(self):
            captured["monitor_stopped"] = True

    class FakeEvaluator:
        def __init__(self, **_kwargs):
            pass

    monkeypatch.setattr(intent_module, "IntentRecognizer", FakeIntentRecognizer)
    monkeypatch.setattr(skill_module, "SkillManager", FakeSkillManager)
    monkeypatch.setattr(agent_module, "AgentOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(verifier_module, "AnswerVerifier", FakeAnswerVerifier)
    monkeypatch.setattr(memory_module, "MemoryManager", FakeMemoryManager)
    monkeypatch.setattr(tool_module, "MCPToolManager", FakeToolManager)
    monkeypatch.setattr(knowledge_module, "KnowledgeBase", FakeKnowledgeBase)
    monkeypatch.setattr(monitor_module, "PerformanceMonitor", FakeMonitor)
    monkeypatch.setattr(evaluation_module, "EndToEndEvaluator", FakeEvaluator)

    # 本机可能存在真实 DeepSeek .env；生命周期测试必须显式隔离供应商配置。
    for name in list(os.environ):
        if name.startswith("MODEL_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
    monkeypatch.setenv("TICKET_DB_PATH", str(tmp_path / "tickets.db"))
    monkeypatch.setenv("RESPONSE_DELIVERY_DB_PATH", str(tmp_path / "responses.db"))
    monkeypatch.delenv("TICKET_DISPATCH_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("CUSTOMER_OPERATIONS_DB_PATH", str(tmp_path / "operations.db"))
    monkeypatch.setenv("REACT_RUN_DB_PATH", str(tmp_path / "react-runs.db"))
    monkeypatch.setenv("AGENT_BUNDLE_DB_PATH", str(tmp_path / "agent-bundles.db"))
    monkeypatch.setenv("REACT_RECOVERY_GRACE_SECONDS", "0")
    monkeypatch.setenv("MEMORY_TOKEN_BUDGET", "4321")
    monkeypatch.setenv("MEMORY_COMPRESSION_THRESHOLD", "0.81")
    monkeypatch.setenv("MEMORY_SUMMARY_MAX_TOKENS", "777")
    monkeypatch.setenv("REACT_MAX_STEPS", "6")
    monkeypatch.setenv("TOOL_APPROVAL_MODE", "require_all")
    monkeypatch.setenv("TOOL_OUTPUT_MAX_CHARS", "2345")
    monkeypatch.setenv("PROMETHEUS_PORT", "0")
    monkeypatch.setenv("CHROMA_MODE", "embedded")
    monkeypatch.setenv("INTENT_SIMILARITY_MODE", "ngram")
    monkeypatch.setenv("INTENT_CACHE_TTL_SECONDS", "987")

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            assert {
                key: value
                for key, value in captured["intent"].items()
                if key not in {"model_profile", "cache_ttl_seconds"}
            } == {
                "api_key": "test-key",
                "base_url": None,
                "model": "claude-3-5-sonnet-20241022",
                "similarity_mode": "ngram",
            }
            assert captured["intent"]["model_profile"].model == "claude-3-5-sonnet-20241022"
            assert captured["intent"]["cache_ttl_seconds"] == 987
            assert captured["memory"]["memory_token_budget"] == 4321
            assert captured["memory"]["compression_threshold"] == 0.81
            assert captured["memory"]["summary_max_tokens"] == 777
            assert captured["memory"]["fact_idle_seconds"] == 300
            assert captured["memory"]["fact_batch_turns"] == 3
            assert captured["memory"]["fact_worker_poll_seconds"] == 5
            assert captured["memory_started"] is True
            assert captured["memory"]["chroma_mode"] == "embedded"
            assert captured["knowledge"]["chroma_mode"] == "embedded"
            assert captured["orchestrator"]["intent_similarity_mode"] == "ngram"
            assert captured["orchestrator"]["react_max_steps"] == 6
            assert captured["orchestrator"]["intent_recognizer"] is not None
            assert captured["orchestrator"]["run_store"] is captured["tool_manager"]["execution_store"]
            assert captured["tool_manager"]["approval_mode"].value == "require_all"
            assert captured["tool_manager"]["max_output_chars"] == 2345
            assert captured["tool_manager_wired"] is True
            assert set(captured["registered_tool_names"]) == {
                "knowledge_search",
                "memory_search",
                "support_ticket_list",
                "support_ticket_get",
                "support_ticket_create",
                "order_lookup",
                "refund_status",
                "refund_eligibility_check",
                "refund_request_create",
                "account_security_event_list",
            }
            knowledge_tool = next(
                tool for tool in main._tool_manager.registered_tools
                if tool.name == "knowledge_search"
            )
            assert knowledge_tool.cache_ttl == 0.0
            assert knowledge_tool.supports_rerank is False
            assert knowledge_tool.output_schema_version == (
                "knowledge-evidence-pack-result-v1"
            )

    asyncio.run(exercise_lifespan())
    assert captured["memory_closed"] is True
    assert captured["monitor_stopped"] is True
"""FastAPI 生命周期依赖装配和配置归属测试。"""
