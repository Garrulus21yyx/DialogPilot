"""PostgreSQL owner for final-response selection, ACK and replay."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from application.delivery_contract import (
    ConnectorCapability,
    DeliveryEvent,
    DeliveryStatusV1,
)
from application.publication import (
    FinalResponseCommand,
    ProjectionDisposition,
    PublicationPolicy,
)
from core.identity import InvocationKey
from infrastructure.postgres import PostgresPool
from infrastructure.postgres_publication import (
    PostgresDeliveryRepository,
    PostgresPublicationService,
)
from services.response_delivery import (
    DeliveryStatus,
    ResponseDelivery,
    ResponseNotFoundError,
)


class PostgresResponseDeliveryService:
    """Expose the ResponseDelivery lifecycle directly from PostgreSQL facts."""

    def __init__(
        self,
        pool: PostgresPool,
        *,
        resume_binding_secret: str,
        clock: Callable[[], str] | None = None,
    ):
        self.pool = pool
        self.publication = PostgresPublicationService(
            pool, resume_binding_secret=resume_binding_secret,
        )
        self.delivery = PostgresDeliveryRepository(pool)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def select_response(
        self,
        *,
        user_id: str,
        conv_id: str,
        request_id: str,
        response_text: str,
        identity_metadata: dict[str, Any] | None = None,
    ) -> ResponseDelivery:
        del request_id
        metadata = dict(identity_metadata or {})
        required = (
            "tenant_id", "invocation_key", "candidate_id", "producer",
            "verifier_status", "verification", "evidence_sha256",
            "bundle_version", "index_manifest_sha256", "expected_state_fingerprint",
        )
        missing = [key for key in required if not metadata.get(key)]
        if missing:
            raise ValueError(f"canonical publication metadata is missing: {missing}")
        now = self.clock()
        invocation_created_at = self._invocation_created_at(
            metadata["invocation_key"], user_id=user_id,
        )
        result = self.publication.select_final_response(FinalResponseCommand(
            invocation_key=InvocationKey(metadata["invocation_key"]),
            tenant_id=metadata["tenant_id"],
            user_id=user_id,
            conversation_id=conv_id,
            response_text=response_text,
            candidate_id=metadata["candidate_id"],
            producer=metadata["producer"],
            verifier_status=metadata["verifier_status"],
            verification=dict(metadata["verification"]),
            evidence_sha256=metadata["evidence_sha256"],
            bundle_version=metadata["bundle_version"],
            index_manifest_sha256=metadata["index_manifest_sha256"],
            created_at=now,
            policy=PublicationPolicy(
                connector_capability=ConnectorCapability.NONE,
                max_attempts=1,
                retry_policy_version="http-client-ack-v1",
                reconcile_deadline=(
                    invocation_created_at + timedelta(days=1)
                ).isoformat(),
            ),
            projection_disposition=ProjectionDisposition(
                metadata.get("projection_disposition") or "normal"
            ),
            public_response=dict(metadata.get("public_response") or {}),
            execution_stages=tuple(
                dict(item) for item in metadata.get("execution_stages") or ()
            ),
            expected_state_fingerprint=metadata["expected_state_fingerprint"],
            knowledge_evidence=tuple(metadata.get("knowledge_evidence") or ()),
            business_observations=tuple(metadata.get("business_observations") or ()),
        ))
        return self._get(result.record.publication_id, user_id=user_id)

    def completed_for_invocation(
        self, invocation_key: InvocationKey, *, user_id: str,
    ):
        """Recover the immutable public response before any regeneration."""
        from application.chat_contracts import Completed, Failed, Reconciling, StageObservation, StageStatus

        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT publication_id, seq, status, payload
                FROM dialogpilot_app.response_deliveries
                WHERE invocation_key=%s AND publication_kind='final_response'
                  AND user_id=%s
            """, (str(invocation_key), user_id)).fetchone()
        if row is None:
            return None
        payload = dict(row[3])
        response = dict(payload.get("public_response") or {})
        response.update({
            "response_id": row[0],
            "response_seq": int(row[1]),
            "delivery_status": _compat_status(DeliveryStatusV1(row[2])),
            "response": payload["response"],
        })
        stages = tuple(StageObservation(
            stage=str(item["stage"]), status=StageStatus(item["status"]),
            detail=dict(item["detail"]),
        ) for item in payload.get("execution_stages") or ())
        if response.get("outcome") == "failed":
            return Failed(str(response["code"]), False, str(response["correlation_id"]),
                          str(payload["response"]), stages, response_id=str(row[0]))
        if response.get("outcome") == "reconciling":
            return Reconciling(str(response["workflow_run_id"]), response,
                               float(response["next_poll_after"]), stages)
        return Completed(str(row[0]), response, stages)

    def acknowledge(
        self,
        response_id: str,
        *,
        user_id: str,
        status: DeliveryStatus,
    ) -> ResponseDelivery:
        target = DeliveryStatus(status)
        if target is DeliveryStatus.SELECTED:
            raise ValueError("client ACK target must be delivered or read")
        self._get(response_id, user_id=user_id)
        now = self.clock()
        self.delivery.apply_event(
            publication_id=response_id,
            receipt_id=f"client-delivered:v1:{response_id}:{user_id}",
            event=DeliveryEvent.RECEIPT_DELIVERED,
            payload={"authenticated_user_id": user_id},
            created_at=now,
        )
        if target is DeliveryStatus.READ:
            self.delivery.apply_event(
                publication_id=response_id,
                receipt_id=f"client-read:v1:{response_id}:{user_id}",
                event=DeliveryEvent.READ_ACK,
                payload={"authenticated_user_id": user_id},
                created_at=now,
            )
        return self._get(response_id, user_id=user_id)

    def list_after(
        self,
        *,
        user_id: str,
        conv_id: str,
        after_seq: int = 0,
        limit: int = 100,
    ) -> list[ResponseDelivery]:
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT publication_id FROM dialogpilot_app.response_deliveries
                WHERE user_id=%s AND conversation_id=%s
                  AND publication_kind='final_response' AND seq > %s
                ORDER BY seq LIMIT %s
            """, (user_id, conv_id, max(0, after_seq), max(1, min(limit, 200)))).fetchall()
        return [self._get(row[0], user_id=user_id) for row in rows]

    def stats(self) -> dict[str, int]:
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT status, count(*) FROM dialogpilot_app.response_deliveries
                WHERE publication_kind='final_response' GROUP BY status
            """).fetchall()
        counts = {status.value: 0 for status in DeliveryStatus}
        for status, count in rows:
            counts[_compat_status(DeliveryStatusV1(status)).value] += int(count)
        return counts

    def _get(self, response_id: str, *, user_id: str) -> ResponseDelivery:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT delivery.publication_id, delivery.user_id,
                       delivery.conversation_id, delivery.seq,
                       delivery.payload->>'response', delivery.status,
                       delivery.created_at, delivery.delivered_at,
                       delivery.read_at, delivery.invocation_key,
                       invocation.request_id
                FROM dialogpilot_app.response_deliveries delivery
                JOIN dialogpilot_app.workflow_invocations invocation
                  ON invocation.invocation_key=delivery.invocation_key
                WHERE delivery.publication_id=%s
                  AND delivery.publication_kind='final_response'
                  AND delivery.user_id=%s
            """, (response_id, user_id)).fetchone()
        if row is None:
            raise ResponseNotFoundError(f"response not found: {response_id}")
        return ResponseDelivery(
            response_id=row[0], user_id=row[1], conv_id=row[2], request_id=row[10],
            seq=int(row[3]), response_text=row[4],
            status=_compat_status(DeliveryStatusV1(row[5])),
            selected_at=row[6].isoformat(),
            delivered_at=row[7].isoformat() if row[7] else None,
            read_at=row[8].isoformat() if row[8] else None,
            identity_metadata={"invocation_key": row[9]},
        )

    def _invocation_created_at(self, invocation_key: str, *, user_id: str):
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT created_at FROM dialogpilot_app.workflow_invocations
                WHERE invocation_key=%s AND user_id=%s
            """, (invocation_key, user_id)).fetchone()
        if row is None:
            raise ResponseNotFoundError("invocation not found")
        return row[0]


def _compat_status(status: DeliveryStatusV1) -> DeliveryStatus:
    if status is DeliveryStatusV1.READ:
        return DeliveryStatus.READ
    if status is DeliveryStatusV1.DELIVERED:
        return DeliveryStatus.DELIVERED
    return DeliveryStatus.SELECTED
