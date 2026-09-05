import asyncio
from dataclasses import replace

import pytest

from application.chat_contracts import Completed, Failed
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
