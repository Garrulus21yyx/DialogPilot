"""Manual handling cannot erase known effects or settle an ambiguous attempt."""
import asyncio
from dataclasses import replace
import itertools

import pytest

from application.agent_result import AgentResultStatus
from application.write_workflow import OperationStatus, WriteOutcomeStatus, WriteToolOutcome
from infrastructure.postgres_target_runtime import operation_from_payload, operation_to_payload
from tests.test_write_recovery import Clock, item_with_policy, outcome, runtime
from tests.test_write_workflow import ledger_factory, _context


def test_operation_effect_contract_rejects_inconsistent_records():
    from application.write_workflow import OperationRecord, WriteWorkflowError

    record = OperationRecord("op", "fingerprint", OperationStatus.PLANNED, 1, 0)
    with pytest.raises(WriteWorkflowError, match="effect knowledge"):
        replace(record, effect_status="NOT_COMMITTED")
    with pytest.raises(WriteWorkflowError, match="disagree"):
        replace(record, status=OperationStatus.COMMITTED, receipt_id="receipt",
                receipt_schema_version="v1")
    with pytest.raises(WriteWorkflowError, match="disagree"):
        replace(record, effect_status=WriteOutcomeStatus.COMMITTED)


@pytest.mark.parametrize("history", ["first_rejection", "unknown_then_replay_rejected", "operation_rejected"])
def test_manual_review_preserves_effect_scope_and_rejection_on_restart(ledger_factory, history):
    async def run():
        item, clock, ledger = item_with_policy(), Clock(), ledger_factory()
        events = []

        class Port:
            async def execute(self, work, **kwargs):
                events.append("write")
                if history != "first_rejection" and events.count("write") == 1:
                    return outcome("OUTCOME_UNKNOWN")
                return WriteToolOutcome(WriteOutcomeStatus.REJECTED, reason_code="STATE_CONFLICT",
                    detail="The object's current state does not allow this change.", source_ref="tool-call-2")

            async def reconcile(self, work, **kwargs):
                events.append("query")
                if history == "operation_rejected":
                    return WriteToolOutcome(WriteOutcomeStatus.REJECTED, reason_code="OWNER_REJECTED",
                        detail="The operation was rejected without changes.", source_ref="operation-lookup")
                return outcome("OUTCOME_UNKNOWN")

        tickets = []
        def review(work, record):
            tickets.append(record)
            return "ticket-1"

        port = Port()
        result = await runtime(item, ledger, port, clock, review)(_context(item))
        record = ledger.acquire(item)
        assert record.status is OperationStatus.MANUAL_REVIEW
        assert result.status is AgentResultStatus.BLOCKED
        expected = (WriteOutcomeStatus.OUTCOME_UNKNOWN if history == "unknown_then_replay_rejected"
                    else WriteOutcomeStatus.NOT_COMMITTED)
        assert record.effect_status is expected
        assert record.last_outcome is WriteOutcomeStatus.REJECTED
        assert record.outcome_scope == ("OPERATION" if history == "operation_rejected" else "ATTEMPT")
        assert record.outcome_detail and record.outcome_source_ref
        assert tickets[0].effect_status is expected
        assert not result.action_receipts
        assert result.execution_feedback[0]["business_outcome"] == (
            "UNCONFIRMED" if expected is WriteOutcomeStatus.OUTCOME_UNKNOWN else "NOT_COMMITTED")
        assert result.execution_feedback[0]["detail"] == record.outcome_detail
        from application.result_board import ResultBoard
        from application.work_item import WorkPlan
        from application.response_assembly import _render_board
        text = _render_board(ResultBoard().evaluate(WorkPlan((item,), item.work_item_id), (result,)), locale="en")
        assert ("was not committed" in text) == (expected is WriteOutcomeStatus.NOT_COMMITTED)
        assert ("outcome is unconfirmed" in text) == (expected is WriteOutcomeStatus.OUTCOME_UNKNOWN)
        assert operation_from_payload(operation_to_payload(record)) == record
        before = list(events)
        replay = await runtime(item, ledger_factory(), port, clock, review)(_context(item))
        assert replay == result
        assert events == before and len(tickets) == 1
        legacy = operation_to_payload(record)
        for key in ("effect_status", "last_outcome", "outcome_scope", "outcome_detail", "outcome_source_ref"):
            del legacy[key]
        restored = operation_from_payload(legacy)
        assert restored.effect_status is WriteOutcomeStatus.OUTCOME_UNKNOWN
        assert restored.last_outcome is None and restored.outcome_detail == ""
    asyncio.run(run())


def test_generated_reconciliation_histories_preserve_operation_knowledge():
    from application.write_workflow import InMemoryOperationLedger

    async def run():
        for sequence in itertools.product(("OUTCOME_UNKNOWN", "REJECTED", "COMMITTED"), repeat=3):
            item, clock, ledger = item_with_policy("RECEIPT_ONLY"), Clock(), InMemoryOperationLedger()

            class Port:
                index = 0

                async def execute(self, work, **kwargs):
                    return outcome("OUTCOME_UNKNOWN")

                async def reconcile(self, work, **kwargs):
                    value = sequence[self.index]
                    self.index += 1
                    return replace(outcome(value), detail=f"Observation {self.index}",
                                   source_ref=f"query:{self.index}")

            port = Port()
            await runtime(item, ledger, port, clock)(_context(item))
            record = ledger.acquire(item)
            terminal = next((value for value in sequence if value != "OUTCOME_UNKNOWN"), "OUTCOME_UNKNOWN")
            expected = "NOT_COMMITTED" if terminal == "REJECTED" else terminal
            assert record.effect_status.value == expected
            assert record.last_outcome.value == terminal
            assert record.outcome_scope == "OPERATION"
            assert record.outcome_source_ref == f"query:{port.index}"
            assert operation_from_payload(operation_to_payload(record)) == record
            if terminal == "COMMITTED":
                legacy = operation_to_payload(record)
                del legacy["effect_status"]
                restored = operation_from_payload(legacy)
                assert restored.effect_status is WriteOutcomeStatus.COMMITTED
                assert restored.receipt_id == record.receipt_id

    asyncio.run(run())


def test_reconciliation_without_automatic_replay_delivers_rejection():
    from application.write_workflow import InMemoryOperationLedger
    from tests.test_write_workflow import _item

    async def run():
        item, clock, ledger = _item(), Clock(), InMemoryOperationLedger()

        class Port:
            async def execute(self, work, **kwargs):
                return outcome("OUTCOME_UNKNOWN")

            async def reconcile(self, work, **kwargs):
                return replace(outcome("REJECTED"), detail="Operation refused.", source_ref="query:original")

        executor = runtime(item, ledger, Port(), clock)
        assert (await executor(_context(item))).status is AgentResultStatus.RECONCILING
        result = await executor(_context(item))
        assert result.status is AgentResultStatus.BLOCKED
        assert result.reason_code == "WRITE_MANUAL_REVIEW_REQUIRED"
        assert result.execution_feedback[0]["business_outcome"] == "NOT_COMMITTED"
        assert result.execution_feedback[0]["detail"] == "Operation refused."
        assert ledger.acquire(item).outcome_scope == "OPERATION"

    asyncio.run(run())


def test_new_dispatch_invalidates_prior_noncommit_knowledge_before_io():
    from application.write_workflow import InMemoryOperationLedger
    from tests.test_write_workflow import _item

    async def run():
        item, ledger, clock = _item(), InMemoryOperationLedger(), Clock()

        class Port:
            calls = 0

            async def execute(self, work, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return replace(outcome("NOT_COMMITTED"), source_ref="first-attempt")
                # The dispatch reservation is durable even if the process stops
                # before it can learn the new request's effect.
                record = ledger.acquire(item)
                assert record.status is OperationStatus.EXECUTING
                assert record.effect_status is WriteOutcomeStatus.OUTCOME_UNKNOWN
                assert record.outcome_source_ref == "first-attempt"
                raise asyncio.CancelledError()

        executor = runtime(item, ledger, Port(), clock)
        await executor(_context(item))
        assert ledger.acquire(item).effect_status is WriteOutcomeStatus.NOT_COMMITTED
        with pytest.raises(asyncio.CancelledError):
            await executor(_context(item))
        record = operation_from_payload(operation_to_payload(ledger.acquire(item)))
        assert record.effect_status is WriteOutcomeStatus.OUTCOME_UNKNOWN
        assert record.last_outcome is WriteOutcomeStatus.NOT_COMMITTED

    asyncio.run(run())


@pytest.mark.parametrize("reason", ["Policy does not allow this state change.", "The selected resource version is obsolete."])
def test_write_adapter_keeps_explicit_rejection_details(reason):
    from infrastructure.target_workflow_execution import _ToolPort
    from mcp.tool_manager import ToolResult
    from tests.test_write_workflow import _item

    class Tools:
        async def execute_for_agent(self, *args, **kwargs):
            return ToolResult(False, {"value": reason}, "refund_request_create",
                status="rejected", effect_status="not_committed", error=reason,
                call_id="rejected-call")

    async def run():
        item = _item()
        outcome = await _ToolPort(Tools(), _context(item), principal="refund").execute(
            item, tool_id="refund_request_create", arguments={}, operation_key=item.operation_key)
        assert outcome.status is WriteOutcomeStatus.REJECTED
        assert outcome.detail == reason
        assert outcome.source_ref == "rejected-call"
        assert outcome.reason_code == "TOOL_REJECTED"
    asyncio.run(run())
