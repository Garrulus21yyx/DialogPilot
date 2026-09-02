"""持久 ReAct checkpoint、身份绑定审批和跨进程幂等恢复测试。"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from agents.react_engine import ReActExecutionEngine, ReActStatus
from agents.run_store import (
    RunAccessDeniedError,
    RunStatus,
    RunStore,
    RunStoreError,
)
from mcp.tool_manager import (
    MCPToolManager,
    Tool,
    ToolCallStatus,
    ToolEffectReceipt,
    ToolEffectStatus,
    ToolRisk,
)


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(call_id, name, arguments):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


class ScriptedClient:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.contents.pop(0))


def _manager(store):
    return MCPToolManager(
        api_key="test-key",
        model="test-model",
        execution_store=store,
    )


def _engine(client, manager, store):
    return ReActExecutionEngine(
        client=client,
        model="test-model",
        tool_manager=manager,
        run_store=store,
        max_steps=4,
    )


def _context(run_id="react-resume-1"):
    return {
        "run_id": run_id,
        "request_id": "request-1",
        "user_id": "user-1",
        "conv_id": "conv-1",
        "task_id": "billing_task",
        "bundle_version": "agent-v1",
    }


def _register_write(manager, effects):
    async def handler(params, _context):
        effects.append(params["order_id"])
        return ToolEffectReceipt(
            data={"accepted": True},
            effect_status=ToolEffectStatus.COMMITTED,
            receipt_id="refund-1",
        )

    manager.register(Tool(
        name="refund_create",
        description="提交退款",
        handler=handler,
        schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        allowed_agents=("billing",),
        risk=ToolRisk.HIGH,
        read_only=False,
        requires_approval=True,
        authority="CustomerOperations",
        output_schema_version="refund-action-v1",
        receipt_schema_version="action-receipt-v1",
    ))


def test_waiting_run_resumes_after_runtime_recreation_and_write_executes_once(tmp_path):
    """新 engine/store 实例从 SQLite 恢复协议消息，并且重复 resume 不重复写。"""
    path = tmp_path / "runs.db"
    first_store = RunStore(str(path))
    first_manager = _manager(first_store)
    effects = []
    _register_write(first_manager, effects)
    client = ScriptedClient([
        [tool_use("call-refund-1", "refund_create", {"order_id": "A-1"})],
        [text("退款申请已提交，回执 refund-1。")],
    ])

    waiting = asyncio.run(_engine(client, first_manager, first_store).run(
        system="billing worker",
        messages=[{"role": "user", "content": "申请退款 A-1"}],
        agent_type="billing",
        execution_context=_context(),
    ))
    assert waiting.status is ReActStatus.WAITING_APPROVAL
    assert effects == []
    assert first_store.get(waiting.run_id).status is RunStatus.WAITING_APPROVAL

    # 模拟进程重启：重新创建 Store、ToolManager 和 Engine。
    resumed_store = RunStore(str(path))
    resumed_manager = _manager(resumed_store)
    _register_write(resumed_manager, effects)
    resumed_engine = _engine(client, resumed_manager, resumed_store)
    completed = asyncio.run(resumed_engine.resume(
        waiting.run_id,
        user_id="user-1",
        approved=True,
        actor="user-1",
    ))

    assert completed.status is ReActStatus.COMPLETED
    assert len(completed.tool_receipts) == 1
    assert completed.tool_receipts[0].receipt_id == "refund-1"
    assert completed.tool_receipts[0].status == "success"
    assert effects == ["A-1"]
    assert resumed_store.get(waiting.run_id).status is RunStatus.COMPLETED
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "call-refund-1"
    assert tool_result["is_error"] is False

    replay = asyncio.run(resumed_engine.resume(
        waiting.run_id,
        user_id="user-1",
        approved=True,
        actor="user-1",
    ))
    assert replay.status is ReActStatus.COMPLETED
    assert replay.tool_receipts == completed.tool_receipts
    assert effects == ["A-1"]


def test_tool_ledger_replays_terminal_result_and_rejects_call_id_rebinding(tmp_path):
    store = RunStore(str(tmp_path / "runs.db"))
    manager = _manager(store)
    effects = []
    _register_write(manager, effects)
    context = _context("react-ledger-1")
    store.create(
        run_id=context["run_id"], request_id=context["request_id"],
        user_id=context["user_id"], conv_id=context["conv_id"],
        agent_type="billing", task_id=context["task_id"], bundle_version="agent-v1",
        system="worker", messages=({"role": "user", "content": "refund"},),
        execution_context=context, max_steps=4,
    )

    first = asyncio.run(manager.execute_for_agent(
        "refund_create", {"order_id": "A-1"}, agent_type="billing",
        context=context, approved=True, call_id="call-fixed",
    ))
    replay = asyncio.run(manager.execute_for_agent(
        "refund_create", {"order_id": "A-1"}, agent_type="billing",
        context=context, approved=True, call_id="call-fixed",
    ))
    rebound = asyncio.run(manager.execute_for_agent(
        "refund_create", {"order_id": "A-2"}, agent_type="billing",
        context=context, approved=True, call_id="call-fixed",
    ))

    assert first.status == replay.status == ToolCallStatus.SUCCESS.value
    assert replay.cached is True
    assert rebound.status == ToolCallStatus.DENIED.value
    assert effects == ["A-1"]


def test_stale_approved_run_recovers_after_crash_without_duplicate_write(tmp_path):
    """模拟写入已提交、Run checkpoint 尚未推进时崩溃；恢复只重放账本结果。"""
    path = tmp_path / "runs.db"
    store = RunStore(str(path), recovery_grace_s=0)
    manager = _manager(store)
    effects = []
    _register_write(manager, effects)
    first_client = ScriptedClient([[
        tool_use("call-crash-window", "refund_create", {"order_id": "A-9"})
    ]])
    waiting = asyncio.run(_engine(first_client, manager, store).run(
        system="worker",
        messages=[{"role": "user", "content": "refund A-9"}],
        agent_type="billing",
        execution_context=_context("react-approved-crash"),
    ))
    acquired = store.acquire_waiting(waiting.run_id, "user-1")
    pending = dict(acquired.pending)
    pending["phase"] = "approved_executing"
    approved_checkpoint = store.checkpoint(
        run_id=waiting.run_id,
        expected_version=acquired.version,
        status=RunStatus.RUNNING,
        messages=acquired.messages,
        runtime={"approval_actor": "user-1", "approval_decision": "approved"},
        step=acquired.step,
        tool_call_ids=acquired.tool_call_ids,
        pending=pending,
    )
    context = dict(approved_checkpoint.execution_context)
    context["run_id"] = waiting.run_id
    committed = asyncio.run(manager.execute_for_agent(
        "refund_create",
        {"order_id": "A-9"},
        agent_type="billing",
        context=context,
        approved=True,
        call_id="call-crash-window",
    ))
    assert committed.status == ToolCallStatus.SUCCESS.value
    assert effects == ["A-9"]

    recovered_client = ScriptedClient([[text("退款申请已提交。")]])
    recovered = asyncio.run(_engine(recovered_client, manager, store).resume(
        waiting.run_id,
        user_id="user-1",
        approved=True,
        actor="user-1",
    ))

    assert recovered.status is ReActStatus.COMPLETED
    assert effects == ["A-9"]
    assert recovered_client.calls[0]["messages"][-1]["content"][0]["is_error"] is False


def test_in_progress_write_is_not_reexecuted_after_uncertain_crash(tmp_path):
    """已占位但无终态的写调用被视为 outcome_unknown，不盲目重试。"""
    store = RunStore(str(tmp_path / "runs.db"))
    manager = _manager(store)
    effects = []
    _register_write(manager, effects)
    context = _context("react-crash-1")
    store.create(
        run_id=context["run_id"], request_id=context["request_id"],
        user_id=context["user_id"], conv_id=context["conv_id"],
        agent_type="billing", task_id=context["task_id"], bundle_version="agent-v1",
        system="worker", messages=({"role": "user", "content": "refund"},),
        execution_context=context, max_steps=4,
    )
    binding = manager._execution_binding_hash(
        name="refund_create", params={"order_id": "A-1"}, agent_type="billing", context=context,
    )
    store.claim_tool_call(
        run_id=context["run_id"], call_id="call-crashed",
        binding_hash=binding, read_only=False,
    )

    result = asyncio.run(manager.execute_for_agent(
        "refund_create", {"order_id": "A-1"}, agent_type="billing",
        context=context, approved=True, call_id="call-crashed",
    ))

    assert result.status == ToolCallStatus.ERROR.value
    assert result.effect_status == ToolEffectStatus.OUTCOME_UNKNOWN.value
    assert effects == []


def test_approval_identity_denial_and_expiry_are_fail_closed(tmp_path):
    path = tmp_path / "runs.db"
    store = RunStore(str(path), approval_ttl_s=60)
    manager = _manager(store)
    effects = []
    _register_write(manager, effects)
    client = ScriptedClient([[tool_use("call-1", "refund_create", {"order_id": "A-1"})]])
    react = _engine(client, manager, store)
    waiting = asyncio.run(react.run(
        system="worker", messages=[{"role": "user", "content": "refund"}],
        agent_type="billing", execution_context=_context("react-identity-1"),
    ))

    with pytest.raises(RunAccessDeniedError):
        asyncio.run(react.resume(
            waiting.run_id, user_id="another-user", approved=True, actor="another-user"
        ))
    denied = asyncio.run(react.resume(
        waiting.run_id, user_id="user-1", approved=False, actor="user-1"
    ))
    assert denied.status is ReActStatus.BLOCKED
    assert effects == []

    expiring_client = ScriptedClient([[tool_use("call-2", "refund_create", {"order_id": "A-2"})]])
    expiring = asyncio.run(_engine(expiring_client, manager, store).run(
        system="worker", messages=[{"role": "user", "content": "refund"}],
        agent_type="billing", execution_context=_context("react-expired-1"),
    ))
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE react_runs SET expires_at=? WHERE run_id=?",
            ("2000-01-01T00:00:00+00:00", expiring.run_id),
        )
    expired = asyncio.run(_engine(ScriptedClient([]), manager, store).resume(
        expiring.run_id, user_id="user-1", approved=True, actor="user-1"
    ))
    assert expired.status is ReActStatus.BLOCKED
    assert store.get(expiring.run_id).status is RunStatus.EXPIRED
    assert effects == []


def test_checkpoint_database_is_owner_read_write_only(tmp_path):
    path = tmp_path / "runs.db"
    store = RunStore(str(path))
    with store._connect():
        pass

    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(f"{path}-wal").st_mode & 0o777 == 0o600
    assert os.stat(f"{path}-shm").st_mode & 0o777 == 0o600


def test_checkpoint_owner_redacts_credentials_before_persistence(tmp_path):
    path = tmp_path / "runs.db"
    store = RunStore(str(path))
    created = store.create(
        run_id="secret-run", request_id="request", user_id="user", conv_id="conv",
        agent_type="billing", task_id="task", bundle_version="bundle",
        system="policy api_key=server-secret-value",
        messages=({"role": "user", "content": "Bearer customer-token-value"},),
        execution_context={
            "password": "database-password-value",
            "authorization_fingerprint": "safe-fingerprint",
        },
        max_steps=2,
    )
    completed = store.checkpoint(
        run_id=created.run_id, expected_version=created.version,
        status=RunStatus.COMPLETED,
        messages=created.messages,
        runtime={"nested": {"refresh_token": "refresh-token-value"}},
        step=1, tool_call_ids=(), result={"text": "token=result-token-value"},
    )

    serialized = repr(completed)
    assert "server-secret-value" not in serialized
    assert "customer-token-value" not in serialized
    assert "database-password-value" not in serialized
    assert "refresh-token-value" not in serialized
    assert "result-token-value" not in serialized
    assert completed.execution_context["authorization_fingerprint"] == "safe-fingerprint"
    assert "[REDACTED]" in serialized


def test_checkpoint_owner_pins_schema_and_rejects_unknown_resume_version(tmp_path):
    path = tmp_path / "runs.db"
    store = RunStore(str(path))
    created = store.create(
        run_id="schema-run", request_id="request", user_id="user", conv_id="conv",
        agent_type="general", task_id="task", bundle_version="bundle",
        system="system", messages=(),
        execution_context={"_checkpoint_schema_version": "caller-forged"},
        max_steps=2,
    )

    assert created.execution_context["_checkpoint_schema_version"] == (
        "react-checkpoint-v1"
    )
    with sqlite3.connect(path) as connection:
        context = dict(created.execution_context)
        context["_checkpoint_schema_version"] = "unknown-v99"
        connection.execute(
            "UPDATE react_runs SET execution_context_json=? WHERE run_id=?",
            (json.dumps(context), created.run_id),
        )

    with pytest.raises(RunStoreError, match="schema/code version is incompatible"):
        store.get(created.run_id)
