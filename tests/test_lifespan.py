import asyncio

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
        def __init__(self, api_key, base_url=None, model=None):
            captured["intent"] = {
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
            }

    class FakeSkillManager:
        def __init__(self, **_kwargs):
            pass

        def load(self):
            return []

    class FakeOrchestrator:
        def __init__(self, **_kwargs):
            pass

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

        async def close(self):
            captured["memory_closed"] = True

    class FakeToolManager:
        def __init__(self, **_kwargs):
            self.tools = []

        def register(self, tool):
            self.tools.append(tool)

        def get_stats(self):
            return {}

    class FakeKnowledgeBase:
        def __init__(self, **_kwargs):
            pass

        async def doc_count_async(self):
            return 0

        async def search_handler(self, _params, _context):
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

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("TICKET_DB_PATH", str(tmp_path / "tickets.db"))
    monkeypatch.setenv("MEMORY_TOKEN_BUDGET", "4321")
    monkeypatch.setenv("MEMORY_COMPRESSION_THRESHOLD", "0.81")
    monkeypatch.setenv("MEMORY_SUMMARY_MAX_TOKENS", "777")
    monkeypatch.setenv("PROMETHEUS_PORT", "0")

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            assert captured["intent"] == {
                "api_key": "test-key",
                "base_url": None,
                "model": "claude-3-5-sonnet-20241022",
            }
            assert captured["memory"]["memory_token_budget"] == 4321
            assert captured["memory"]["compression_threshold"] == 0.81
            assert captured["memory"]["summary_max_tokens"] == 777
            assert captured["tool_manager_wired"] is True

    asyncio.run(exercise_lifespan())
    assert captured["memory_closed"] is True
    assert captured["monitor_stopped"] is True
"""FastAPI 生命周期依赖装配和配置归属测试。"""
