import asyncio
from dataclasses import replace
from typing import get_args

import pytest

from application.chat_contracts import (
    Accepted, Cancelled, ChatOutcome, Completed, Conflict, Expired, Failed,
    HandedOff, NeedsInput, Reconciling, Rejected,
)
from application.target_run import (
    TargetRunClaimLost,
    TargetRunItem,
    TargetRunTerminal,
    TargetRunWorker,
    outcome_from_terminal,
    terminal_from_outcome,
)
from core.identity import IdentityFactory


class _RunStore:
    def __init__(self, item):
        self.item = item
        self.available = True
        self.owner = item.claimed_by
        self.attempt = item.attempt
        self.terminal_value = None
        self.releases = []

    def claim(self, *, worker_id, lease_seconds, limit=1, invocation_key=None):
        del lease_seconds, limit
        if not self.available or (
            invocation_key is not None
            and invocation_key != self.item.invocation_key
        ):
            return ()
        self.available = False
        self.owner = worker_id
        self.item = replace(
            self.item, claimed_by=worker_id, attempt=self.attempt,
        )
        return (self.item,)

    def assert_owned(self, item, *, worker_id):
        if worker_id != self.owner or item.attempt != self.attempt:
            raise TargetRunClaimLost("stale target run claim")

    def renew(self, item, *, worker_id, lease_seconds):
        del lease_seconds
        self.assert_owned(item, worker_id=worker_id)

    def complete(self, item, *, worker_id, terminal):
        self.assert_owned(item, worker_id=worker_id)
        self.terminal_value = terminal

    def release(
        self, item, *, worker_id, error_code, retry_after_seconds=0,
    ):
        self.assert_owned(item, worker_id=worker_id)
        self.releases.append((error_code, retry_after_seconds))
        self.available = True

    def terminal(self, invocation_key):
        assert invocation_key == self.item.invocation_key
        return self.terminal_value

    def takeover(self):
        self.attempt += 1
        self.owner = "replacement-worker"


def test_completion_wait_reads_terminal_without_claiming_or_executing_work():
    from application.chat_contracts import Accepted, Completed
    from application.target_run import TargetRunCoordinator, terminal_from_outcome

    store = _RunStore(_item())
    coordinator = TargetRunCoordinator(None, dispatcher=None, store=store)
    result = Completed("p1", {"response": "查询完成"})

    async def run():
        async def publish():
            await asyncio.sleep(0.01)
            store.terminal_value = terminal_from_outcome(result)

        task = asyncio.create_task(publish())
        outcome = await coordinator.await_outcome(
            Accepted("run", {"invocation_key": str(store.item.invocation_key)}),
            timeout_seconds=1, poll_seconds=0.001,
        )
        await task
        return outcome

    assert asyncio.run(run()) == result
    assert store.available
    assert not store.releases


def test_completion_wait_timeout_leaves_original_work_untouched():
    from application.chat_contracts import Accepted
    from application.target_run import TargetRunCoordinator

    store = _RunStore(_item())
    coordinator = TargetRunCoordinator(None, dispatcher=None, store=store)
    with pytest.raises(TimeoutError):
        asyncio.run(coordinator.await_outcome(
            Accepted("run", {"invocation_key": str(store.item.invocation_key)}),
            timeout_seconds=0.01, poll_seconds=0.001,
        ))
    assert store.available
    assert not store.releases
    assert store.terminal_value is None


def _item():
    identity = IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-a",
        user_id="user-a",
        conversation_id="conversation-a",
        request_id="request-a",
    )
    return TargetRunItem(
        run_id=identity.workflow_run_id,
        invocation_key=identity.invocation_key,
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        request_id=str(identity.request_id),
        continuation_id=str(identity.continuation_id),
        message="查订单",
        asset_ids=(),
        pinned_versions={"authorization_fingerprint": "auth"},
        deletion_epoch=0,
        attempt=1,
        claimed_by="worker-a",
    )


def test_target_run_worker_commits_one_terminal_and_replays_its_outcome():
    store = _RunStore(_item())
    expected = Completed("response-1", {"response": "完成"})

    async def execute(_item, guard):
        await guard()
        return expected

    worker = TargetRunWorker(
        store, execute, lease_seconds=20, heartbeat_seconds=2,
    )
    assert asyncio.run(worker.run_once(worker_id="worker-a")) == (expected,)
    assert store.terminal_value == terminal_from_outcome(expected)
    assert outcome_from_terminal(store.terminal_value) == expected
    assert asyncio.run(worker.run_once(worker_id="worker-b")) == ()


def test_retryable_failure_requeues_same_run_without_terminal_completion():
    store = _RunStore(_item())
    failure = Failed("provider_timeout", True, "correlation-1")

    async def execute(_item, _guard):
        return failure

    worker = TargetRunWorker(
        store, execute, lease_seconds=20, heartbeat_seconds=2,
        retry_after_seconds=3,
    )
    assert asyncio.run(worker.run_once(worker_id="worker-a")) == (failure,)
    assert store.terminal_value is None
    assert store.releases == [("provider_timeout", 3)]


def test_stale_attempt_cannot_commit_after_another_worker_takes_ownership():
    store = _RunStore(_item())

    async def execute(_item, _guard):
        store.takeover()
        return Completed("response-1", {"response": "完成"})

    worker = TargetRunWorker(
        store, execute, lease_seconds=20, heartbeat_seconds=2,
    )
    with pytest.raises(TargetRunClaimLost, match="stale target run claim"):
        asyncio.run(worker.run_once(worker_id="worker-a"))
    assert store.terminal_value is None


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        ("REJECTED", {"code": "unsupported", "safe_message": "不支持"}),
        (
            "RECONCILING",
            {
                "workflow_run_id": "run-1",
                "public_status": {"status": "unknown"},
                "next_poll_after": 1.0,
            },
        ),
    ],
)
def test_persisted_terminal_algebra_is_reconstructable(status, payload):
    outcome = outcome_from_terminal(TargetRunTerminal(status, payload, "ref-1"))
    assert terminal_from_outcome(outcome).status == status


TERMINAL_OUTCOMES = (
    Completed("response-1", {"response": "完成", "facts": ["receipt:1"]}),
    NeedsInput("run-1", "signal-1", "INPUT", "2030-01-01", "question-1"),
    NeedsInput("run-1", "signal-2", "APPROVAL", "2030-01-01", "question-2"),
    HandedOff("ticket-1", "handoff-1"),
    Cancelled("run-1", "USER_CANCELLED"),
    Expired("run-1", "RUNTIME", False),
    Reconciling("run-1", {"operation_key": "operation-1"}, 2.5),
    Rejected("UNSUPPORTED", "暂不支持"),
    Conflict("INPUT_CONFLICT", {"invocation_key": "original"}),
    Failed("TOOL_ERROR", False, "trace-1", "查询失败"),
)


@pytest.mark.parametrize("outcome", TERMINAL_OUTCOMES)
def test_target_terminal_roundtrip_preserves_public_outcome(outcome):
    terminal = terminal_from_outcome(outcome)
    assert outcome_from_terminal(terminal) == outcome
    assert terminal_from_outcome(outcome_from_terminal(terminal)) == terminal


def test_target_outcome_union_is_covered_without_legacy_runtime_states():
    assert {type(outcome) for outcome in TERMINAL_OUTCOMES} | {Accepted} == set(
        get_args(ChatOutcome)
    )
    for outcome in (Accepted("run-1", {}), Failed("RETRY", True, "trace-1")):
        with pytest.raises(ValueError, match="non-terminal target outcome"):
            terminal_from_outcome(outcome)
    with pytest.raises(ValueError, match="unknown target run terminal status"):
        outcome_from_terminal(TargetRunTerminal("UNKNOWN", {}, "unknown"))
