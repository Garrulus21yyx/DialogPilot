import json
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from memory.context import ContextAssembler, ContextSection, TokenEstimator
from memory.conversation_memory import MemoryManager, Message, MsgRole
from core.model_policy import ModelProfile


def raw_message(role: str, content: str) -> str:
    return json.dumps({
        "role": role,
        "content": content,
        "ts": datetime.now().isoformat(),
        "metadata": {},
    })


def bare_manager(*, budget=512, threshold=0.7, summary_tokens=128):
    manager = MemoryManager.__new__(MemoryManager)
    manager._memory_token_budget = budget
    manager._compression_threshold = threshold
    manager._summary_max_tokens = summary_tokens
    manager._model_profile = ModelProfile("test-model")
    manager._token_estimator = TokenEstimator()
    manager._compression_stats = {
        "attempted": 0,
        "completed": 0,
        "conflicts": 0,
        "failures": 0,
        "tokens_before": 0,
        "tokens_after": 0,
    }
    return manager


@pytest.mark.parametrize(
    "short,long",
    [
        ("你好", "你好" * 200),
        ("hello", "hello " * 200),
        ("订单 A1", "订单 A1 登录失败且重复扣款。" * 100),
    ],
)
def test_token_estimator_is_monotonic_for_repeated_content(short, long):
    """证明内容增长不会导致估算 Token 反向减少。"""
    estimator = TokenEstimator()
    assert estimator.estimate(long) > estimator.estimate(short) > 0


def test_context_assembler_preserves_real_turns_and_bounds_prompt():
    """证明装配器保留真实最近对话，并把总输入限制在预算内。"""
    assembler = ContextAssembler(
        max_input_tokens=700,
        reserved_output_tokens=100,
        fixed_system_reserve=100,
    )
    history = [
        {"role": "user", "content": f"old question {i} " * 10}
        if i % 2 == 0
        else {"role": "assistant", "content": f"old answer {i} " * 10}
        for i in range(20)
    ]
    prompt = assembler.assemble(
        sections=[
            ContextSection("summary", "important summary " * 30, priority=100),
            ContextSection("knowledge", "retrieved data " * 100, priority=50),
        ],
        history=history,
        current_user_message="current question",
    )
    messages = prompt.to_messages("current question")

    assert prompt.estimated_tokens <= assembler.max_input_tokens
    assert messages[-1] == {"role": "user", "content": "current question"}
    assert all(message["content"] != "好的，我已了解背景信息。" for message in messages)
    assert prompt.dropped_history > 0
    assert "<summary" in prompt.system_context


def test_compression_trigger_uses_tokens_not_message_count():
    """证明压缩权威触发条件是 Token，而不是消息条数。"""
    manager = bare_manager(budget=512, threshold=0.7)
    manager._redis = FakeRedis([raw_message("user", "超长问题" * 300)])

    assert asyncio.run(manager._needs_compression("u", "c")) is True

    manager._redis = FakeRedis([raw_message("user", "短") for _ in range(20)])
    assert asyncio.run(manager._needs_compression("u", "c")) is False


def test_structured_rolling_summary_stays_valid_and_bounded():
    """证明滚动摘要始终是结构化 JSON，且不会继续无界增长。"""
    manager = bare_manager(summary_tokens=128)
    payload = {
        "user_goal": "处理登录和扣款" * 100,
        "confirmed_facts": [f"fact-{i}-" * 30 for i in range(20)],
        "pending_questions": [f"question-{i}" for i in range(20)],
        "entities": {f"key-{i}": "value" * 20 for i in range(20)},
        "decisions": [f"decision-{i}" for i in range(20)],
        "user_preferences": [f"preference-{i}" for i in range(20)],
    }

    summary = manager._bounded_summary(payload)
    parsed = json.loads(summary)

    assert set(parsed) == {
        "user_goal",
        "confirmed_facts",
        "pending_questions",
        "entities",
        "decisions",
        "user_preferences",
    }
    assert manager._token_estimator.estimate(summary) <= manager._summary_max_tokens


def test_concurrent_write_prevents_stale_compression_commit():
    """证明压缩期间出现并发写时旧快照不能覆盖新消息。"""
    manager = bare_manager(budget=512, threshold=0.5)
    original = [
        raw_message("assistant" if i % 2 else "user", "历史内容" * 80)
        for i in range(8)
    ]
    redis = FakeRedis(original)
    manager._redis = redis
    manager._client = MutatingClient(redis)
    manager._model = "test-model"

    archived = []

    async def record_archive(*args, **_kwargs):
        archived.append(args)
        return True

    manager._archive_messages = record_archive
    asyncio.run(manager._compress("user", "conversation"))

    assert redis.values[0] == MutatingClient.concurrent_message
    assert len(redis.values) == len(original) + 1
    assert redis.summary == ""
    assert manager.compression_stats()["conflicts"] == 1
    assert len(archived) == 1


def test_successful_compression_replaces_summary_and_preserves_recent_messages():
    """证明无冲突提交会替换摘要，同时保留受保护的最近轮次。"""
    manager = bare_manager(budget=512, threshold=0.5, summary_tokens=128)
    original = [
        raw_message("assistant" if i % 2 else "user", f"历史-{i}-" * 80)
        for i in range(8)
    ]
    redis = FakeRedis(original)
    manager._redis = redis
    manager._client = StableClient()
    manager._model = "test-model"
    stored = []

    async def record_store(*args, **_kwargs):
        stored.append(args)
        return True

    manager._archive_messages = record_store
    asyncio.run(manager._compress("user", "conversation"))

    assert 2 <= len(redis.values) <= manager.RECENT_KEEP
    assert redis.values == original[: len(redis.values)]
    assert json.loads(redis.summary)["user_goal"] == "解决问题"
    assert manager._token_estimator.estimate(redis.summary) <= manager._summary_max_tokens
    assert len(stored) == 1
    assert manager.compression_stats()["completed"] == 1


def test_finalize_archives_short_session_then_clears_working_memory():
    """证明未达到压缩阈值的短会话也能显式进入长期记忆。"""
    manager = bare_manager()
    redis = FakeRedis([
        raw_message("assistant", "已为你记录"),
        raw_message("user", "订单 A123 重复扣款"),
    ])
    redis.summary = '{"user_goal":"处理扣款"}'
    manager._redis = redis
    archived = []

    async def record_archive(_user_id, _conv_id, messages, **kwargs):
        archived.append((messages, kwargs))
        return True

    manager._archive_messages = record_archive
    result = asyncio.run(manager.finalize_conversation("user", "short-conversation"))

    assert result == {"archived_messages": 2, "finalized": True, "already_empty": False}
    assert [message.role for message in archived[0][0]] == [MsgRole.USER, MsgRole.ASSISTANT]
    assert archived[0][1]["reason"] == "conversation_finalize"
    assert redis.values == []
    assert redis.summary == ""

    repeated = asyncio.run(manager.finalize_conversation("user", "short-conversation"))
    assert repeated == {"archived_messages": 0, "finalized": True, "already_empty": True}


def test_finalize_concurrent_write_preserves_redis_for_retry():
    """证明归档快照之后的新消息不会被会话结束操作误删。"""
    manager = bare_manager()
    redis = FakeRedis([raw_message("user", "原消息")])
    manager._redis = redis

    async def archive_then_receive_new_message(*_args, **_kwargs):
        redis.values.insert(0, raw_message("assistant", "并发到达的新消息"))
        return True

    manager._archive_messages = archive_then_receive_new_message
    result = asyncio.run(manager.finalize_conversation("user", "conversation"))

    assert result["finalized"] is False
    assert result["reason"] == "concurrent_write"
    assert len(redis.values) == 2


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def watch(self, *_keys):
        return None

    async def lrange(self, _key, start, end):
        return await self.redis.lrange(_key, start, end)

    async def get(self, _key):
        return self.redis.summary

    async def unwatch(self):
        return None

    def multi(self):
        return None

    def delete(self, *keys):
        self.commands.append(("delete", keys))

    def rpush(self, key, *values):
        self.commands.append(("rpush", key, values))

    def expire(self, key, ttl):
        self.commands.append(("expire", key, ttl))

    def setex(self, key, ttl, value):
        self.commands.append(("setex", key, ttl, value))

    async def execute(self):
        for command in self.commands:
            if command[0] == "delete":
                if any(str(key).startswith("wm:") for key in command[1]):
                    self.redis.values = []
                if any(str(key).startswith("summary:") for key in command[1]):
                    self.redis.summary = ""
            elif command[0] == "rpush":
                self.redis.values.extend(command[2])
            elif command[0] == "setex":
                self.redis.summary = command[3]


class FakeRedis:
    def __init__(self, values):
        self.values = list(values)
        self.summary = ""

    async def lrange(self, _key, start, end):
        if end == -1:
            return list(self.values[start:])
        return list(self.values[start : end + 1])

    async def get(self, _key):
        return self.summary

    def pipeline(self, transaction=True):
        assert transaction is True
        return FakePipeline(self)


class MutatingClient:
    concurrent_message = raw_message("user", "压缩期间到达的新消息")

    def __init__(self, redis):
        self.redis = redis
        self.messages = self

    async def create(self, **_kwargs):
        self.redis.values.insert(0, self.concurrent_message)
        payload = {
            "user_goal": "解决问题",
            "confirmed_facts": ["已有事实"],
            "pending_questions": [],
            "entities": {},
            "decisions": [],
            "user_preferences": [],
        }
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))])


class StableClient:
    def __init__(self):
        self.messages = self

    async def create(self, **_kwargs):
        payload = {
            "user_goal": "解决问题",
            "confirmed_facts": ["已有事实"],
            "pending_questions": [],
            "entities": {},
            "decisions": [],
            "user_preferences": [],
        }
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))])
"""Token 预算、结构化摘要与并发压缩提交的不变量测试。"""
