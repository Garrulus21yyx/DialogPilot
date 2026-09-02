"""Canonical cross-owner evidence references issued by governed adapters only."""
from __future__ import annotations

import hashlib
import json
from dataclasses import InitVar, asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, TypeAlias

from application.authority_policy import (
    AuthorityContractError,
    AuthorityPolicyRegistry,
)
from application.hybrid_retrieval import RetrievalStatus
from application.media_evidence import MediaLocator
from mcp.tool_manager import ToolCallStatus, ToolEffectStatus


class EvidenceContractError(ValueError):
    pass


class EvidenceKind(str, Enum):
    KNOWLEDGE = "KNOWLEDGE"
    BUSINESS_TOOL = "BUSINESS_TOOL"
    ACTION_RECEIPT = "ACTION_RECEIPT"
    MEMORY_EVENT = "MEMORY_EVENT"
    SERVICE_EPISODE = "SERVICE_EPISODE"
    COMMITMENT = "COMMITMENT"
    MEDIA_OBSERVATION = "MEDIA_OBSERVATION"
    HUMAN_ASSERTION = "HUMAN_ASSERTION"


class RequirementStatus(str, Enum):
    SATISFIED = "SATISFIED"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"
    STALE = "STALE"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


class MemoryEventStatus(str, Enum):
    OBSERVED = "OBSERVED"
    DELETED = "DELETED"
    INVALID_CONTRACT = "INVALID_CONTRACT"


class CommitmentEvidenceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    FULFILLED = "FULFILLED"
    BREACHED = "BREACHED"
    CANCELLED = "CANCELLED"
    INVALID_CONTRACT = "INVALID_CONTRACT"


class MediaObservationStatus(str, Enum):
    OBSERVED = "OBSERVED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INVALID_CONTRACT = "INVALID_CONTRACT"


class HumanAssertionStatus(str, Enum):
    ASSERTED = "ASSERTED"
    RETRACTED = "RETRACTED"
    INVALID_CONTRACT = "INVALID_CONTRACT"


def _required(*values: str) -> None:
    if any(not str(value).strip() for value in values):
        raise EvidenceContractError("locator provenance must not be blank")


def _sha256(value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise EvidenceContractError("checksum must be lowercase SHA-256")


@dataclass(frozen=True)
class KnowledgeLocator:
    tenant_id: str
    backend_id: str
    generation_id: str
    scope: str
    locale: str
    product: str | None
    source_id: str
    source_revision: str
    source_checksum: str
    start_char: int
    end_char: int

    def __post_init__(self) -> None:
        _required(
            self.tenant_id, self.backend_id, self.generation_id,
            self.scope, self.locale, self.source_id, self.source_revision,
        )
        _sha256(self.source_checksum)
        if self.start_char < 0 or self.end_char <= self.start_char:
            raise EvidenceContractError("knowledge span is invalid")


@dataclass(frozen=True)
class BusinessToolLocator:
    call_id: str
    tool_name: str
    object_ref: str
    object_version: str

    def __post_init__(self) -> None:
        _required(self.call_id, self.tool_name, self.object_ref, self.object_version)


@dataclass(frozen=True)
class ActionReceiptLocator:
    call_id: str
    tool_name: str
    receipt_id: str

    def __post_init__(self) -> None:
        _required(self.call_id, self.tool_name, self.receipt_id)


@dataclass(frozen=True)
class MemoryEventLocator:
    memory_id: str
    conversation_id: str
    event_seq: int

    def __post_init__(self) -> None:
        _required(self.memory_id, self.conversation_id)
        if self.event_seq < 1:
            raise EvidenceContractError("memory event sequence must be positive")


@dataclass(frozen=True)
class ServiceEpisodeLocator:
    tenant_id: str
    user_id: str
    backend_id: str
    generation_id: str
    episode_id: str
    episode_revision: str
    provenance_sha256: str

    def __post_init__(self) -> None:
        _required(
            self.tenant_id, self.user_id, self.backend_id, self.generation_id,
            self.episode_id, self.episode_revision,
        )
        _sha256(self.provenance_sha256)


@dataclass(frozen=True)
class CommitmentLocator:
    commitment_id: str
    version: int

    def __post_init__(self) -> None:
        _required(self.commitment_id)
        if self.version < 1:
            raise EvidenceContractError("commitment version must be positive")


MediaObservationLocator = MediaLocator


@dataclass(frozen=True)
class HumanAssertionLocator:
    assertion_id: str
    principal_ref: str
    channel_event_ref: str

    def __post_init__(self) -> None:
        _required(self.assertion_id, self.principal_ref, self.channel_event_ref)


EvidenceLocator: TypeAlias = (
    KnowledgeLocator | BusinessToolLocator | ActionReceiptLocator
    | MemoryEventLocator | ServiceEpisodeLocator | CommitmentLocator
    | MediaObservationLocator
    | HumanAssertionLocator
)
EvidenceStatus: TypeAlias = (
    RetrievalStatus | ToolCallStatus | ToolEffectStatus | MemoryEventStatus
    | CommitmentEvidenceStatus | MediaObservationStatus | HumanAssertionStatus
)


_LOCATOR_BY_KIND = {
    EvidenceKind.KNOWLEDGE: KnowledgeLocator,
    EvidenceKind.BUSINESS_TOOL: BusinessToolLocator,
    EvidenceKind.ACTION_RECEIPT: ActionReceiptLocator,
    EvidenceKind.MEMORY_EVENT: MemoryEventLocator,
    EvidenceKind.SERVICE_EPISODE: ServiceEpisodeLocator,
    EvidenceKind.COMMITMENT: CommitmentLocator,
    EvidenceKind.MEDIA_OBSERVATION: MediaObservationLocator,
    EvidenceKind.HUMAN_ASSERTION: HumanAssertionLocator,
}
_STATUS_BY_KIND = {
    EvidenceKind.KNOWLEDGE: RetrievalStatus,
    EvidenceKind.BUSINESS_TOOL: ToolCallStatus,
    EvidenceKind.ACTION_RECEIPT: ToolEffectStatus,
    EvidenceKind.MEMORY_EVENT: MemoryEventStatus,
    EvidenceKind.SERVICE_EPISODE: RetrievalStatus,
    EvidenceKind.COMMITMENT: CommitmentEvidenceStatus,
    EvidenceKind.MEDIA_OBSERVATION: MediaObservationStatus,
    EvidenceKind.HUMAN_ASSERTION: HumanAssertionStatus,
}


class EvidenceSchema:
    """Closed kind/locator/status algebra independent of producer availability."""

    @staticmethod
    def validate(
        kind: EvidenceKind,
        locator: EvidenceLocator,
        status: EvidenceStatus,
    ) -> None:
        if not isinstance(kind, EvidenceKind):
            raise EvidenceContractError("unknown evidence kind")
        if not isinstance(locator, _LOCATOR_BY_KIND[kind]):
            raise EvidenceContractError("evidence locator kind mismatch")
        if not isinstance(status, _STATUS_BY_KIND[kind]):
            raise EvidenceContractError("evidence status domain mismatch")


_ISSUER_TOKEN = object()


@dataclass(frozen=True)
class EvidenceReceipt:
    receipt_id: str
    schema_version: str
    kind: EvidenceKind
    requirement_id: str
    authority: str
    producer_id: str
    producer_version: str
    adapter_id: str
    adapter_version: str
    policy_version: str
    policy_fingerprint: str
    locator: EvidenceLocator
    status: EvidenceStatus
    observed_at: datetime
    expires_at: datetime | None
    field_names: tuple[str, ...]
    content_sha256: str
    receipt_sha256: str
    issuer_token: InitVar[object]

    def __post_init__(self, issuer_token: object) -> None:
        if issuer_token is not _ISSUER_TOKEN:
            raise EvidenceContractError("receipt must be issued by a registered adapter")
        _required(
            self.receipt_id, self.schema_version, self.requirement_id,
            self.authority, self.producer_id, self.producer_version,
            self.adapter_id, self.adapter_version, self.policy_version,
            self.policy_fingerprint,
        )
        _sha256(self.policy_fingerprint)
        _sha256(self.content_sha256)
        _sha256(self.receipt_sha256)
        EvidenceSchema.validate(self.kind, self.locator, self.status)
        if self.observed_at.tzinfo is None:
            raise EvidenceContractError("observed_at must be timezone-aware")
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at <= self.observed_at
        ):
            raise EvidenceContractError("expires_at must follow observed_at")
        if tuple(sorted(set(self.field_names))) != self.field_names:
            raise EvidenceContractError("field names must be sorted and unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            **_receipt_body(
                schema_version=self.schema_version,
                kind=self.kind,
                requirement_id=self.requirement_id,
                authority=self.authority,
                producer_id=self.producer_id,
                producer_version=self.producer_version,
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                policy_version=self.policy_version,
                policy_fingerprint=self.policy_fingerprint,
                locator=self.locator,
                status=self.status,
                observed_at=self.observed_at,
                expires_at=self.expires_at,
                field_names=self.field_names,
                content_sha256=self.content_sha256,
            ),
            "receipt_id": self.receipt_id,
            "receipt_sha256": self.receipt_sha256,
        }


class EvidenceReceiptIssuer:
    schema_version = "evidence-receipt-v1"

    def __init__(self, policies: AuthorityPolicyRegistry):
        self._policies = policies

    def adapter(
        self,
        adapter_id: str,
        adapter_version: str,
    ) -> "RegisteredEvidenceAdapter":
        self._policies.get_evidence_adapter(adapter_id, adapter_version)
        return RegisteredEvidenceAdapter(
            self, adapter_id, adapter_version, _ISSUER_TOKEN
        )

    def _issue(
        self,
        *,
        requirement_id: str,
        adapter_id: str,
        adapter_version: str,
        producer_id: str,
        producer_version: str,
        locator: EvidenceLocator,
        status: EvidenceStatus,
        observed_at: datetime,
        payload: Mapping[str, Any],
    ) -> EvidenceReceipt:
        adapter = self._policies.authorize_evidence_adapter(
            requirement_id=requirement_id,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            producer_id=producer_id,
            producer_version=producer_version,
        )
        kind = EvidenceKind(adapter.evidence_kind)
        EvidenceSchema.validate(kind, locator, status)
        if not isinstance(payload, Mapping):
            raise EvidenceContractError("diagnostic text cannot be evidence")
        requirement = self._policies.get(requirement_id)
        payload_fields = {str(field) for field in payload}
        field_names = tuple(sorted(requirement.required_fields))
        missing = set(field_names).difference(payload_fields)
        if missing:
            raise EvidenceContractError(
                "evidence payload omits required fields: "
                + ",".join(sorted(missing))
            )
        if not _locator_binds_payload(locator, producer_id, payload):
            raise EvidenceContractError("locator does not bind evidence payload")
        if observed_at.tzinfo is None:
            raise EvidenceContractError("observed_at must be timezone-aware")
        expires_at = (
            observed_at + timedelta(seconds=requirement.freshness_seconds)
            if requirement.freshness_seconds is not None else None
        )
        content_sha256 = _payload_sha256(payload)
        body = _receipt_body(
            schema_version=self.schema_version,
            kind=kind,
            requirement_id=requirement_id,
            authority=requirement.authority,
            producer_id=producer_id,
            producer_version=producer_version,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            policy_version=self._policies.version,
            policy_fingerprint=self._policies.fingerprint,
            locator=locator,
            status=status,
            observed_at=observed_at,
            expires_at=expires_at,
            field_names=field_names,
            content_sha256=content_sha256,
        )
        receipt_sha256 = _json_sha256(body)
        return EvidenceReceipt(
            receipt_id=f"evidence:v1:{receipt_sha256[:32]}",
            receipt_sha256=receipt_sha256,
            issuer_token=_ISSUER_TOKEN,
            **{
                key: value for key, value in body.items()
                if key not in {
                    "kind", "locator", "status", "observed_at", "expires_at",
                    "field_names",
                }
            },
            kind=kind,
            locator=locator,
            status=status,
            observed_at=observed_at,
            expires_at=expires_at,
            field_names=field_names,
        )

    def restore(self, value: Mapping[str, Any]) -> EvidenceReceipt:
        try:
            kind = EvidenceKind(str(value["kind"]))
            locator_type = _LOCATOR_BY_KIND[kind]
            locator = locator_type(**dict(value["locator"]))
            status = _STATUS_BY_KIND[kind](str(value["status"]))
            observed_at = datetime.fromisoformat(str(value["observed_at"]))
            expires_at = (
                datetime.fromisoformat(str(value["expires_at"]))
                if value.get("expires_at") else None
            )
            requirement_id = str(value["requirement_id"])
            adapter_id = str(value["adapter_id"])
            adapter_version = str(value["adapter_version"])
            producer_id = str(value["producer_id"])
            adapter = self._policies.authorize_evidence_adapter(
                requirement_id=requirement_id,
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                producer_id=producer_id,
                producer_version=str(value["producer_version"]),
            )
            requirement = self._policies.get(requirement_id)
            if kind.value != adapter.evidence_kind:
                raise EvidenceContractError("receipt evidence kind mismatch")
            if str(value["schema_version"]) != adapter.receipt_schema_version:
                raise EvidenceContractError("receipt schema version mismatch")
            if str(value["authority"]) != requirement.authority:
                raise EvidenceContractError("receipt authority mismatch")
            if (
                str(value["policy_version"]) != self._policies.version
                or str(value["policy_fingerprint"]) != self._policies.fingerprint
            ):
                raise EvidenceContractError("receipt policy provenance mismatch")
            if tuple(value["field_names"]) != tuple(sorted(requirement.required_fields)):
                raise EvidenceContractError("receipt required fields mismatch")
            body = {key: value[key] for key in _BODY_KEYS}
            expected_sha256 = _json_sha256(body)
            if value["receipt_sha256"] != expected_sha256:
                raise EvidenceContractError("receipt checksum mismatch")
            if value["receipt_id"] != f"evidence:v1:{expected_sha256[:32]}":
                raise EvidenceContractError("receipt ID mismatch")
            return EvidenceReceipt(
                receipt_id=str(value["receipt_id"]),
                schema_version=str(value["schema_version"]),
                kind=kind,
                requirement_id=requirement_id,
                authority=str(value["authority"]),
                producer_id=producer_id,
                producer_version=str(value["producer_version"]),
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                policy_version=str(value["policy_version"]),
                policy_fingerprint=str(value["policy_fingerprint"]),
                locator=locator,
                status=status,
                observed_at=observed_at,
                expires_at=expires_at,
                field_names=tuple(value["field_names"]),
                content_sha256=str(value["content_sha256"]),
                receipt_sha256=str(value["receipt_sha256"]),
                issuer_token=_ISSUER_TOKEN,
            )
        except (KeyError, TypeError, ValueError, AuthorityContractError) as exc:
            if isinstance(exc, EvidenceContractError):
                raise
            raise EvidenceContractError("invalid evidence receipt") from exc


class RegisteredEvidenceAdapter:
    """A fixed registry-issued conversion boundary; identity is not caller input."""

    def __init__(
        self,
        issuer: EvidenceReceiptIssuer,
        adapter_id: str,
        adapter_version: str,
        token: object,
    ):
        if token is not _ISSUER_TOKEN:
            raise EvidenceContractError("adapter must come from policy registry")
        self._issuer = issuer
        self.adapter_id = adapter_id
        self.adapter_version = adapter_version

    def issue(
        self,
        *,
        requirement_id: str,
        producer_id: str,
        producer_version: str,
        locator: EvidenceLocator,
        status: EvidenceStatus,
        observed_at: datetime,
        payload: Mapping[str, Any],
    ) -> EvidenceReceipt:
        return self._issuer._issue(
            requirement_id=requirement_id,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            producer_id=producer_id,
            producer_version=producer_version,
            locator=locator,
            status=status,
            observed_at=observed_at,
            payload=payload,
        )


class EvidenceResolver(Protocol):
    def resolve(self, locator: EvidenceLocator) -> Mapping[str, Any]: ...


class EvidenceReceiptVerifier:
    def verify(
        self,
        receipt: EvidenceReceipt,
        resolver: EvidenceResolver,
        *,
        now: datetime | None = None,
    ) -> RequirementStatus:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise EvidenceContractError("verification clock must be timezone-aware")
        if receipt.expires_at is not None and current > receipt.expires_at:
            return RequirementStatus.STALE
        try:
            payload = resolver.resolve(receipt.locator)
        except Exception:
            return RequirementStatus.INVALID_EVIDENCE
        if not isinstance(payload, Mapping):
            return RequirementStatus.INVALID_EVIDENCE
        if _payload_sha256(payload) != receipt.content_sha256:
            return RequirementStatus.INVALID_EVIDENCE
        if not set(receipt.field_names).issubset(str(field) for field in payload):
            return RequirementStatus.INVALID_EVIDENCE
        if not _locator_binds_payload(
            receipt.locator, receipt.producer_id, payload
        ):
            return RequirementStatus.INVALID_EVIDENCE
        return _requirement_status_for_evidence(receipt.status)


_BODY_KEYS = (
    "schema_version", "kind", "requirement_id", "authority", "producer_id",
    "producer_version", "adapter_id", "adapter_version", "policy_version",
    "policy_fingerprint", "locator", "status", "observed_at", "expires_at",
    "field_names", "content_sha256",
)


def _receipt_body(**values: Any) -> dict[str, Any]:
    body = dict(values)
    body["kind"] = values["kind"].value
    body["locator"] = asdict(values["locator"])
    body["status"] = values["status"].value
    body["observed_at"] = values["observed_at"].isoformat()
    body["expires_at"] = (
        values["expires_at"].isoformat() if values["expires_at"] else None
    )
    body["field_names"] = list(values["field_names"])
    return body


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    return _json_sha256(dict(payload))


def _requirement_status_for_evidence(status: EvidenceStatus) -> RequirementStatus:
    if status in {
        RetrievalStatus.INVALID_CONTRACT,
        MemoryEventStatus.INVALID_CONTRACT,
        CommitmentEvidenceStatus.INVALID_CONTRACT,
        MediaObservationStatus.INVALID_CONTRACT,
        HumanAssertionStatus.INVALID_CONTRACT,
    }:
        return RequirementStatus.INVALID_EVIDENCE
    if status is RetrievalStatus.CONFLICT:
        return RequirementStatus.CONFLICTING
    if status in {
        RetrievalStatus.OK,
        ToolCallStatus.SUCCESS,
        ToolEffectStatus.COMMITTED,
        MemoryEventStatus.OBSERVED,
        CommitmentEvidenceStatus.ACTIVE,
        CommitmentEvidenceStatus.FULFILLED,
        CommitmentEvidenceStatus.BREACHED,
        CommitmentEvidenceStatus.CANCELLED,
        MediaObservationStatus.OBSERVED,
        HumanAssertionStatus.ASSERTED,
    }:
        return RequirementStatus.SATISFIED
    return RequirementStatus.MISSING


def _locator_binds_payload(
    locator: EvidenceLocator,
    producer_id: str,
    payload: Mapping[str, Any],
) -> bool:
    if isinstance(locator, KnowledgeLocator):
        return (
            str(payload.get("source_id") or "") == locator.source_id
            and str(payload.get("source_revision") or "") == locator.source_revision
            and str(payload.get("checksum") or "") == locator.source_checksum
        )
    if isinstance(locator, BusinessToolLocator):
        identity_values = {
            str(value) for key, value in payload.items()
            if str(key).endswith("_id") and value is not None
        }
        version_values = {
            str(payload[key]) for key in ("version", "order_version", "updated_at", "occurred_at")
            if key in payload and payload[key] is not None
        }
        return (
            locator.tool_name == producer_id
            and locator.object_ref in identity_values
            and locator.object_version in version_values
        )
    if isinstance(locator, ActionReceiptLocator):
        receipt_ref = str(
            payload.get("receipt_id") or payload.get("refund_id")
            or payload.get("ticket_id") or ""
        )
        return locator.tool_name == producer_id and receipt_ref == locator.receipt_id
    if isinstance(locator, MemoryEventLocator):
        return (
            str(payload.get("memory_id") or "") == locator.memory_id
            and str(payload.get("conversation_id") or "") == locator.conversation_id
            and str(payload.get("event_seq") or "") == str(locator.event_seq)
        )
    if isinstance(locator, ServiceEpisodeLocator):
        return (
            str(payload.get("tenant_id") or "") == locator.tenant_id
            and str(payload.get("user_id") or "") == locator.user_id
            and str(payload.get("backend_id") or "") == locator.backend_id
            and str(payload.get("generation_id") or "") == locator.generation_id
            and str(payload.get("episode_id") or "") == locator.episode_id
            and str(payload.get("episode_revision") or "")
            == locator.episode_revision
            and str(payload.get("provenance_sha256") or "")
            == locator.provenance_sha256
        )
    if isinstance(locator, CommitmentLocator):
        return (
            str(payload.get("commitment_id") or "") == locator.commitment_id
            and str(payload.get("version") or "") == str(locator.version)
        )
    if isinstance(locator, MediaObservationLocator):
        return (
            str(payload.get("asset_id") or "") == locator.asset_id
            and str(payload.get("checksum") or "") == locator.asset_checksum
            and int(payload.get("page_index", -1)) == locator.page_index
            and str(payload.get("coordinate_space") or "")
            == locator.coordinate_space.value
            and tuple(payload.get("bbox") or ()) == locator.bbox
        )
    if isinstance(locator, HumanAssertionLocator):
        return (
            str(payload.get("assertion_id") or "") == locator.assertion_id
            and str(payload.get("principal_ref") or "") == locator.principal_ref
            and str(payload.get("channel_event_ref") or "")
            == locator.channel_event_ref
        )
    return False


def _json_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError("evidence payload is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()
