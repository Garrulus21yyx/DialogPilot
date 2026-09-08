"""Bounded recovery invariants, persistence, cancellation and owner guarantees."""
import asyncio
from dataclasses import replace
import itertools
from uuid import uuid4

import pytest

from application.agent_result import AgentResultStatus
from application.capability_registry import WriteRecoveryPolicy, CapabilityRegistryError
from application.write_workflow import (
    GovernedWriteRuntime, OperationConflict, OperationStatus,
    WriteOutcomeStatus, WriteToolOutcome,
)
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from infrastructure.postgres_target_runtime import (
    _work_item_to_payload, _work_item_from_payload, operation_to_payload, operation_from_payload,
)
from tests.test_write_workflow import ledger_factory, _item, _context, _grant


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.waits.append(delay)
        self.now += max(delay, .01)
        await asyncio.sleep(0)


def item_with_policy(mode="IDEMPOTENT_OPERATION", **changes):
    item = _item(uuid4().hex)
    return replace(item, reconciliation=replace(item.reconciliation,
        recovery=WriteRecoveryPolicy(mode, **changes)))


def runtime(item, ledger, port, clock, review=None):
    return GovernedWriteRuntime(ledger=ledger, tool_port=port, reconciliation_port=port,
        approval_grants={item.approval_binding: _grant(operation_key=item.operation_key)},
        manual_review=review, clock=clock, sleep=clock.sleep)


def outcome(value):
    return WriteToolOutcome(WriteOutcomeStatus(value), reason_code=value,
        receipt_id="receipt" if value == "COMMITTED" else "",
        receipt_schema_version="refund-receipt-v1" if value == "COMMITTED" else "")


@pytest.mark.parametrize("mode", ["RECEIPT_ONLY", "IDEMPOTENT_OPERATION"])
def test_generated_query_sequences_have_bounded_monotonic_recovery(ledger_factory, mode):
    async def run():
        # 4^3 traces include unavailable reads, missing receipts, authoritative
        # receipts and explicit rejection, at every recovery position.
        for sequence in itertools.product(("OUTCOME_UNKNOWN", "COMMITTED", "REJECTED", "UNAVAILABLE"), repeat=3):
            item, clock = item_with_policy(mode), Clock()
            ledger = ledger_factory()
            events, tickets = [], set()

            class Port:
                index = 0
                async def execute(self, work, **kwargs):
                    assert work.operation_fingerprint == item.operation_fingerprint
                    assert kwargs["operation_key"] == item.operation_key
                    events.append("write")
                    return outcome("OUTCOME_UNKNOWN")

                async def reconcile(self, work, **kwargs):
                    events.append("query")
                    value = sequence[self.index]
                    self.index += 1
                    if value == "UNAVAILABLE":
                        raise TimeoutError("read unavailable")
                    return outcome(value)

            def review(work, record):
                tickets.add(record.operation_key)
                return "ticket:" + record.operation_key

            port = Port()
            result = await runtime(item, ledger, port, clock, review)(_context(item))
            record = ledger.acquire(item)
            terminal = next((v for v in sequence if v in {"COMMITTED", "REJECTED"}), None)
            assert (result.status is AgentResultStatus.SUCCEEDED) == (terminal == "COMMITTED")
            assert record.recovery_attempts == port.index <= 3
            assert record.attempts == events.count("write") <= 4
            assert events[0] == "write"
            assert all(events[i-1] == "query" for i, event in enumerate(events) if i and event == "write")
            if mode == "RECEIPT_ONLY":
                assert events.count("write") == 1
            before = list(events)
            again = await runtime(item, ledger_factory(), port, clock, review)(_context(item))
            assert again.status is result.status and events == before
            assert len(tickets) == (0 if terminal == "COMMITTED" else 1)
            assert operation_from_payload(operation_to_payload(record)) == record
    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["query", "replay", "ticket"])
def test_restart_preserves_claim_budget_and_idempotent_review(ledger_factory, boundary):
    async def run():
        item, clock = item_with_policy(), Clock()
        calls, tickets = [], set()
        armed = True

        class Port:
            async def execute(self, work, **kwargs):
                nonlocal armed
                calls.append("write")
                if boundary == "replay" and calls.count("write") == 2 and armed:
                    armed = False
                    raise asyncio.CancelledError()
                return outcome("OUTCOME_UNKNOWN")

            async def reconcile(self, work, **kwargs):
                nonlocal armed
                calls.append("query")
                if boundary == "query" and armed:
                    armed = False
                    raise asyncio.CancelledError()
                return outcome("OUTCOME_UNKNOWN")

        def review(work, record):
            nonlocal armed
            tickets.add(record.operation_key)
            if boundary == "ticket" and armed:
                armed = False
                raise RuntimeError("crash after ticket commit")
            return "ticket:" + record.operation_key

        port = Port()
        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await runtime(item, ledger_factory(), port, clock, review)(_context(item))
        saved = ledger_factory().acquire(item)
        assert saved.recovery_attempts >= 1
        restored_item = _work_item_from_payload(_work_item_to_payload(item))
        result = await runtime(restored_item, ledger_factory(), port, clock, review)(_context(restored_item))
        record = ledger_factory().acquire(item)
        assert result.status is AgentResultStatus.BLOCKED
        assert record.status is OperationStatus.MANUAL_REVIEW
        assert record.recovery_attempts == 3
        assert len(tickets) == 1 and record.manual_ticket_id
        assert calls.count("write") <= 4 and calls.count("query") <= 3
        assert sum(clock.waits) >= 1
    asyncio.run(run())


def test_recovery_capability_is_pinned_in_operation_and_checkpoint(ledger_factory):
    item = item_with_policy("RECEIPT_ONLY")
    ledger = ledger_factory()
    ledger.acquire(item)
    changed = replace(item, reconciliation=replace(item.reconciliation,
        recovery=WriteRecoveryPolicy("IDEMPOTENT_OPERATION")))
    assert changed.operation_fingerprint != item.operation_fingerprint
    with pytest.raises(OperationConflict):
        ledger.acquire(changed)
    saver = target_checkpoint_serializer()
    assert saver.loads_typed(saver.dumps_typed(item)) == item
    assert _work_item_from_payload(_work_item_to_payload(item)) == item
    legacy = replace(item, reconciliation=replace(item.reconciliation, recovery=None))
    assert "recovery" not in _work_item_to_payload(legacy)["reconciliation"]
    assert _work_item_from_payload(_work_item_to_payload(legacy)) == legacy


@pytest.mark.parametrize("kwargs", [{"mode": "invented"}, {"max_attempts": 0},
    {"max_attempts": True}, {"delay_seconds": float("nan")}, {"delay_seconds": 6}])
def test_unsupported_recovery_policy_fails_closed(kwargs):
    with pytest.raises(CapabilityRegistryError):
        WriteRecoveryPolicy(**kwargs)


def test_storage_cannot_reset_recovery_budget_or_replace_manual_ticket(ledger_factory):
    from application.write_workflow import WriteWorkflowError
    item = item_with_policy()
    ledger = ledger_factory()
    initial = ledger.acquire(item)
    saved = replace(initial, version=2, status=OperationStatus.MANUAL_REVIEW,
                    recovery_attempts=3, attempts=2, manual_ticket_id="ticket")
    assert ledger.compare_and_set(initial, saved)
    for changes in ({"recovery_attempts": 0}, {"attempts": 0}, {"manual_ticket_id": "other"}):
        with pytest.raises(WriteWorkflowError):
            ledger.compare_and_set(saved, replace(saved, version=3, **changes))
    assert ledger_factory().acquire(item) == saved
