"""Idempotent manual-review handoff for an unresolved business operation."""
import hashlib
import json

from infrastructure.postgres_ticket_service import PostgresTicketService
from services.ticket_service import TicketPriority


class PostgresWriteRecoveryReview:
    def __init__(self, pool, scope):
        self._tickets = PostgresTicketService(pool)
        self._scope = scope

    def __call__(self, item, record):
        scope = self._scope
        identity = [str(scope.tenant_id), str(scope.user_id), str(scope.conversation_id), record.operation_key]
        key = "write-recovery:" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        # Canonical request content is stable across processes and turn-local IDs.
        # A crash after ticket insertion and before operation CAS reuses this ticket.
        ticket, _ = self._tickets.create_ticket(idempotency_key=key,
            user_id=str(scope.user_id), conv_id=str(scope.conversation_id), request_id=key,
            question=json.dumps({"operation_key": record.operation_key,
                "operation_fingerprint": record.work_item_fingerprint,
                "action_ref": item.action_ref, "target": item.aggregate_ref,
                "target_version": item.target_entity_version,
                "effect_status": record.effect_status.value,
                "last_outcome": record.last_outcome.value if record.last_outcome else None,
                "outcome_scope": record.outcome_scope,
                "outcome_detail": record.outcome_detail,
                "outcome_source_ref": record.outcome_source_ref,
                "arguments": {arg.name: arg.value for arg in item.arguments}},
                sort_keys=True, ensure_ascii=False),
            published_response=("业务请求未提交，自动执行已停止，等待人工核查。"
                if record.effect_status.value == "NOT_COMMITTED"
                else "业务结果尚未确认，自动执行已停止，等待人工核实。"),
            reason="WRITE_MANUAL_REVIEW_REQUIRED", priority=TicketPriority.HIGH,
            agent_type=item.owner_agent, verification_status="unknown",
            identity_metadata={"tenant_id": str(scope.tenant_id), "operation_key": record.operation_key})
        return ticket.ticket_id
