import asyncio
import os
from types import SimpleNamespace

from api import main


def test_lifespan_wires_memory_budget_to_memory_owner(
    tmp_path, monkeypatch, postgres_database_url,
):
    """证明 Memory 配置只传给 MemoryManager，不会误传给意图识别器。"""
    """The API boundary must send memory policy to MemoryManager, not intent."""
    import core.intent_recognizer as intent_module
    import core.skill_loader as skill_module
    import evaluation.evaluator as evaluation_module
    import infrastructure.postgres_knowledge_store as knowledge_module
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

    class FakeAnswerVerifier:
        def __init__(self, *_args, **_kwargs):
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
            from types import SimpleNamespace
            self.llm_client = SimpleNamespace(messages=SimpleNamespace(), beta=SimpleNamespace(messages=SimpleNamespace()))
            self._query_transformer = SimpleNamespace(standalone=None)
            self._result_reranker = SimpleNamespace(rerank=None)

        def register(self, tool):
            self.tools.append(tool)
            captured.setdefault("registered_tool_names", []).append(tool.name)

        def tools_for_agent(self, agent_type, *, allowed_tool_ids):
            return tuple(tool for tool in self.tools if tool.name in allowed_tool_ids
                         and (not tool.allowed_agents or agent_type in tool.allowed_agents))

        @property
        def registered_tools(self):
            return tuple(self.tools)

        @property
        def registered_tool_names(self):
            return tuple(tool.name for tool in self.tools)

        def get_stats(self):
            return {}

    class FakeKnowledgeStore:
        def __init__(self, *_args, **kwargs):
            captured["knowledge"] = kwargs

        async def ensure_defaults_async(self):
            return self.active_generation()

        async def doc_count_async(self):
            return 0

        def active_generation(self):
            return SimpleNamespace(
                manifest_hash="a" * 64,
                generation_id="knowledge-generation-test",
                backend_fingerprint="POSTGRES_PGVECTOR_PG_FTS_ZH_V1",
            )

        @property
        def index_manifest(self):
            return {"manifest_fingerprint": "a" * 64}

        def validate_cached_candidates(self, _candidates):
            return True

        def validate_publication_evidence(self, packs):
            return True

        def validate_current_evidence(self, packs):
            return True

        def embed_query(self, _query, _generation):
            return (0.0,)

        async def search_variants_async(self, *_args, **_kwargs):
            return []

    class FakeMonitor:
        def __init__(self, **kwargs):
            captured["monitor"] = kwargs
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
    monkeypatch.setattr(verifier_module, "AnswerVerifier", FakeAnswerVerifier)
    monkeypatch.setattr(memory_module, "MemoryManager", FakeMemoryManager)
    monkeypatch.setattr(tool_module, "MCPToolManager", FakeToolManager)
    monkeypatch.setattr(
        knowledge_module, "PostgresKnowledgeStore", FakeKnowledgeStore,
    )
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
    monkeypatch.setenv("DATABASE_URL", postgres_database_url)
    monkeypatch.delenv("TICKET_DISPATCH_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("MEMORY_TOKEN_BUDGET", "4321")
    monkeypatch.setenv("MEMORY_COMPRESSION_THRESHOLD", "0.81")
    monkeypatch.setenv("MEMORY_SUMMARY_MAX_TOKENS", "777")
    monkeypatch.setenv("REACT_MAX_STEPS", "6")
    monkeypatch.setenv("TOOL_APPROVAL_MODE", "require_all")
    monkeypatch.setenv("PROMETHEUS_PORT", "0")
    monkeypatch.setenv("INTENT_SIMILARITY_MODE", "ngram")
    monkeypatch.setenv("INTENT_CACHE_TTL_SECONDS", "987")

    from infrastructure.postgres import PostgresMigrationRunner
    PostgresMigrationRunner(postgres_database_url).upgrade()

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            assert all(worker._skill_manager is main._skill_manager
                       for worker in main._target_orchestration._domain_workers.values())
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
            assert captured["memory"]["fact_store"].backend == {
                "mode": "postgres",
                "location": "dialogpilot_app.memory_facts",
            }
            assert captured["knowledge"]["tenant_id"] == "default"
            assert "execution_store" not in captured["tool_manager"]
            assert main._bundle_registry.pool is main._postgres_pool
            assert main._target_run_coordinator is not None
            assert captured["monitor"]["execution_runtime"] is main._target_orchestration
            assert captured["tool_manager"]["approval_mode"].value == "require_all"
            assert "max_output_chars" not in captured["tool_manager"]
            assert set(captured["registered_tool_names"]) == {
                "read_conversation_observation",
                "knowledge_search",
                "service_episode_search",
                "support_ticket_list",
                "support_ticket_get",
                "support_ticket_by_operation",
                "support_ticket_create",
                "commitment_list",
                "order_lookup",
                "order_cancel_status",
                "order_cancel",
                "refund_status",
                "refund_eligibility_check",
                "refund_request_create",
                "account_security_event_list",
                "account_security_state",
                "account_freeze",
                "account_freeze_status",
                "shipping_address_change",
                "shipping_address_change_status",
                "media_read",
                "media_observe",
                "catalog_search",
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
            episode_tool = next(
                tool for tool in main._tool_manager.registered_tools
                if tool.name == "service_episode_search"
            )
            calls = []

            class Search:
                def search(self, **kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(to_dict=lambda: {
                        "status": "OK", "hits": [{"episode_id": "case-1"}],
                        "detail_code": None,
                    })

            main._service_episode_search = Search()
            output = await episode_tool.handler(
                {"query": "E401", "entity_ids": ["device-1"], "top_k": 2},
                {"tenant_id": "tenant-1", "user_id": "user-1"},
            )
            assert output["hits"][0]["episode_id"] == "case-1"
            assert calls == [{
                "tenant_id": "tenant-1", "user_id": "user-1",
                "query": "E401", "entity_ids": ("device-1",),
                "purpose": "HISTORICAL_EVIDENCE",
                "explicit_time_reference": False,
                "top_k": 2,
            }]

    asyncio.run(exercise_lifespan())
    assert captured["memory_closed"] is True
    assert captured["monitor_stopped"] is True
"""FastAPI 生命周期依赖装配和配置归属测试。"""
