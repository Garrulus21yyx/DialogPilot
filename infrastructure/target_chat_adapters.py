"""PostgreSQL admission/publication adapters for Target v1 `/chat`."""
from __future__ import annotations

from datetime import datetime, timezone

from application.admission_contract import (
    AdmissionConflict,
    AdmissionCreated,
    ExecutionPointer,
)
from application.chat_application import ChatCommand
from application.inbound_admission import NewInvocationInbound
from application.target_chat_application import (
    PublishedTargetResponse,
    TargetAdmission,
    TargetAdmissionStatus,
)
from core.identity import InvocationIdentity
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork


class PostgresTargetAdmission:
    def __init__(self, pool) -> None:
        self._admission = PostgresAdmissionUnitOfWork(pool)

    def admit(self, command, identity, *, bundle_version):
        created_at = datetime.now(timezone.utc).isoformat()
        result = self._admission.admit_synchronous(
            NewInvocationInbound(
                identity,
                command.message,
                {
                    "bundle_version": bundle_version,
                    "target_runtime_version": "target-chat-application-v1",
                    "authorization_fingerprint": command.authorization_fingerprint,
                },
                created_at,
                asset_ids=command.asset_ids,
            ),
            ExecutionPointer(
                "target-langgraph",
                "target-conversation-manager-v1",
                str(identity.invocation_key),
            ),
        )
        if isinstance(result, AdmissionConflict):
            return TargetAdmission(
                TargetAdmissionStatus.CONFLICT,
                {
                    "invocation_key": str(result.existing.invocation_key),
                    "workflow_run_id": str(result.existing.workflow_run_id),
                    "status": result.existing.status.value,
                },
            )
        return TargetAdmission(
            TargetAdmissionStatus.CREATED
            if isinstance(result, AdmissionCreated)
            else TargetAdmissionStatus.EXISTING,
        )


class PostgresTargetPublication:
    def __init__(self, response_delivery) -> None:
        self._delivery = response_delivery

    def completed(self, identity):
        return self._delivery.completed_for_invocation(
            identity.invocation_key,
            user_id=str(identity.user_id),
        )

    def publish(
        self,
        identity,
        *,
        response_text,
        public_response,
        bundle_version,
        evidence_sha256,
        verifier_status,
    ):
        selected = self._delivery.select_response(
            user_id=str(identity.user_id),
            conv_id=str(identity.conversation_id),
            request_id=str(identity.request_id),
            response_text=response_text,
            identity_metadata={
                **identity.metadata(),
                "candidate_id": f"target:{identity.invocation_key}",
                "producer": "target-chat-application-v1",
                "verifier_status": verifier_status,
                "verification": {
                    "status": verifier_status,
                    "policy": "target-result-board-v1",
                },
                "evidence_sha256": evidence_sha256,
                "bundle_version": bundle_version,
                "index_manifest_sha256": "not-applicable",
                "public_response": dict(public_response),
            },
        )
        return PublishedTargetResponse(
            selected.response_id,
            selected.seq,
            selected.status.value,
        )
