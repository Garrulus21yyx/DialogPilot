"""PostgreSQL admission/publication adapters for Target v1 `/chat`."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from application.admission_contract import (
    AdmissionConflict,
    AdmissionCreated,
    ExecutionPointer,
)
from application.chat_contracts import ChatCommand
from application.inbound_admission import NewInvocationInbound
from application.delivery_contract import ConnectorCapability
from application.publication import (
    InteractionRequestCommand,
    ProjectionDisposition,
    PublicationPolicy,
)
from application.target_chat_application import (
    PublishedTargetResponse,
    TargetAdmission,
    TargetAdmissionStatus,
)
from core.identity import InvocationIdentity
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork


_NO_INDEX_MANIFEST_SHA256 = hashlib.sha256(
    b"dialogpilot:target:no-index-manifest:v1"
).hexdigest()


class PostgresTargetAdmission:
    def __init__(self, pool, *, durable: bool = False) -> None:
        self._admission = PostgresAdmissionUnitOfWork(pool)
        self._durable = durable

    def admit(self, command, identity, *, bundle_version):
        created_at = datetime.now(timezone.utc).isoformat()
        inbound = NewInvocationInbound(
            identity,
            command.message,
            {
                "bundle_version": bundle_version,
                "target_runtime_version": "target-chat-application-v1",
                "authorization_fingerprint": command.authorization_fingerprint,
                "approval_id": command.approval_id or "",
                "approval_decision": (
                    "approved" if command.approval_decision is True
                    else "declined" if command.approval_decision is False
                    else "none"
                ),
                "interaction_id": command.interaction_id or "",
                "interaction_version": str(command.interaction_version or ""),
                "interaction_values": json.dumps(
                    command.interaction_values,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
            created_at,
            asset_ids=command.asset_ids,
            runtime_kind="target",
        )
        result = (
            self._admission.admit_new(inbound)
            if self._durable
            else self._admission.admit_synchronous(
                inbound,
                ExecutionPointer(
                    "target-langgraph",
                    "target-conversation-manager-v1",
                    str(identity.invocation_key),
                ),
            )
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

    def has_interaction(self, identity, *, signal_id, signal_version):
        with self._delivery.pool.transaction() as connection:
            return connection.execute("""
                SELECT 1 FROM dialogpilot_app.response_deliveries
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
                  AND publication_kind='interaction_request'
                  AND ((signal_id=%s AND signal_version=%s)
                       OR payload->'related_signals' @> %s::jsonb)
            """, (str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id),
                  signal_id, signal_version, json.dumps([{"signal_id": signal_id, "signal_version": signal_version}]))).fetchone() is not None

    def completed(self, identity):
        completed = self._delivery.completed_for_invocation(
            identity.invocation_key,
            user_id=str(identity.user_id),
        )
        if completed is not None:
            return completed
        with self._delivery.pool.transaction() as connection:
            row = connection.execute("""
                SELECT publication_id, signal_id, signal_version,
                       reconcile_deadline, payload
                FROM dialogpilot_app.response_deliveries
                WHERE invocation_key=%s AND user_id=%s
                  AND publication_kind='interaction_request'
                ORDER BY seq DESC LIMIT 1
            """, (str(identity.invocation_key), str(identity.user_id))).fetchone()
        if row is None:
            return None
        from application.chat_contracts import NeedsInput, StageObservation, StageStatus
        payload = dict(row[4] or {})
        kind = str(dict(payload.get("resume_schema") or {}).get(
            "interaction_kind", "APPROVAL",
        ))
        return NeedsInput(
            str(identity.workflow_run_id), str(row[1]), kind,
            row[3].isoformat(), str(row[0]),
            stages=tuple(StageObservation(stage["stage"], StageStatus(stage["status"]), stage["detail"])
                         for stage in payload.get("execution_stages", ())),
        )

    def publish_interaction(
        self,
        identity,
        *,
        signal_id,
        signal_version,
        challenge,
        resume_schema,
        expires_at,
        expected_work_controls=(),
        related_signals=(),
        execution_stages=(),
        knowledge_evidence=(),
        business_observations=(),
    ):
        now = datetime.now(timezone.utc).isoformat()
        result = self._delivery.publication.publish_interaction_request(
            InteractionRequestCommand(
                identity.invocation_key,
                str(identity.tenant_id),
                str(identity.user_id),
                str(identity.conversation_id),
                signal_id,
                signal_version,
                challenge,
                dict(resume_schema),
                now,
                PublicationPolicy(
                    ConnectorCapability.NONE,
                    1,
                    "http-client-approval-v1",
                    expires_at,
                ),
                (
                    ProjectionDisposition.CLARIFICATION
                    if resume_schema.get("interaction_kind") == "FIELDS"
                    else ProjectionDisposition.APPROVAL
                ),
                expected_work_controls=tuple(expected_work_controls),
                related_signals=tuple(related_signals),
                execution_stages=tuple(stage.to_dict() for stage in execution_stages),
                knowledge_evidence=tuple(knowledge_evidence),
                business_observations=tuple(business_observations),
            )
        )
        return PublishedTargetResponse(
            result.record.publication_id,
            result.record.seq,
            result.record.status.value,
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
        expected_work_controls=(),
        execution_stages=(),
        knowledge_evidence=(),
        business_observations=(),
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
                    "policy": "target-answer-verification-v1",
                    "reason_code": public_response["verification_reason_code"],
                    "verified": public_response["verified"],
                },
                "evidence_sha256": evidence_sha256,
                "knowledge_evidence": tuple(knowledge_evidence),
                "business_observations": tuple(business_observations),
                "bundle_version": bundle_version,
                "index_manifest_sha256": _NO_INDEX_MANIFEST_SHA256,
                "public_response": dict(public_response),
                "execution_stages": [item.to_dict() for item in execution_stages],
                "expected_work_controls": [
                    dict(item.__dict__) for item in expected_work_controls
                ],
            },
        )
        return PublishedTargetResponse(
            selected.response_id,
            selected.seq,
            selected.status.value,
        )
