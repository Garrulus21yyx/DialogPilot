"""Strict inventory/backfill eligibility for legacy episodic memory."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Mapping, Sequence


class BackfillDisposition(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    RETAIN_CONVERSATION_ONLY = "RETAIN_CONVERSATION_ONLY"


@dataclass(frozen=True)
class LegacyEpisodeRecord:
    legacy_id: str
    tenant_id: str
    user_id: str
    conversation_id: str
    source_event_refs: tuple[str, ...]
    case_id: str
    case_status: str
    outcome_verification_ref: str
    outcome_verification_status: str
    role: str
    content_sha256: str
    deletion_epoch: int = 0
    tombstoned: bool = False

    def __post_init__(self) -> None:
        if not self.legacy_id.strip() or self.deletion_epoch < 0:
            raise ValueError("legacy episode inventory identity is invalid")
        if self.content_sha256 and len(self.content_sha256) != 64:
            raise ValueError("legacy content hash must be SHA-256")


@dataclass(frozen=True)
class BackfillDecision:
    legacy_id: str
    disposition: BackfillDisposition
    reason_codes: tuple[str, ...]
    episode_id: str | None


@dataclass(frozen=True)
class LegacyEpisodeInventoryReport:
    policy_version: str
    source_watermark: str
    record_count: int
    eligible_count: int
    retained_count: int
    inventory_sha256: str
    decisions: tuple[BackfillDecision, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "source_watermark": self.source_watermark,
            "record_count": self.record_count,
            "eligible_count": self.eligible_count,
            "retained_count": self.retained_count,
            "inventory_sha256": self.inventory_sha256,
            "decisions": [{
                **asdict(item), "disposition": item.disposition.value,
            } for item in self.decisions],
        }


class ServiceEpisodeBackfillPolicy:
    version = "service-episode-backfill-policy-v1"

    def decide(self, record: LegacyEpisodeRecord) -> BackfillDecision:
        reasons = []
        checks = (
            (not record.tenant_id.strip(), "MISSING_TENANT"),
            (not record.user_id.strip(), "MISSING_USER"),
            (not record.conversation_id.strip(), "MISSING_CONVERSATION"),
            (not record.source_event_refs, "MISSING_SOURCE_EVENTS"),
            (not record.case_id.strip(), "MISSING_CASE"),
            (record.case_status not in {"resolved", "closed"}, "CASE_NOT_CLOSED"),
            (not record.outcome_verification_ref.strip(), "MISSING_OUTCOME_VERIFICATION"),
            (
                record.outcome_verification_status != "ACCEPTED",
                "OUTCOME_NOT_ACCEPTED",
            ),
            (record.tombstoned, "SUBJECT_TOMBSTONED"),
        )
        reasons.extend(code for failed, code in checks if failed)
        if reasons:
            return BackfillDecision(
                record.legacy_id, BackfillDisposition.RETAIN_CONVERSATION_ONLY,
                tuple(reasons), None,
            )
        return BackfillDecision(
            record.legacy_id, BackfillDisposition.ELIGIBLE,
            ("CASE_OWNER_VERIFIED",), record.case_id,
        )

    def inventory(
        self, records: Sequence[LegacyEpisodeRecord], *, source_watermark: str,
    ) -> LegacyEpisodeInventoryReport:
        if not source_watermark.strip():
            raise ValueError("legacy inventory watermark is required")
        ordered = tuple(sorted(records, key=lambda item: item.legacy_id))
        if len({item.legacy_id for item in ordered}) != len(ordered):
            raise ValueError("legacy inventory IDs must be unique")
        decisions = tuple(self.decide(item) for item in ordered)
        digest = hashlib.sha256(json.dumps(
            [asdict(item) for item in ordered], ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        eligible = sum(
            item.disposition is BackfillDisposition.ELIGIBLE for item in decisions
        )
        return LegacyEpisodeInventoryReport(
            self.version, source_watermark, len(ordered), eligible,
            len(ordered) - eligible, digest, decisions,
        )


def legacy_record(
    legacy_id: str, document: str, metadata: Mapping[str, object],
) -> LegacyEpisodeRecord:
    """Parse only explicit legacy metadata; never infer Case truth from text."""
    refs = metadata.get("source_event_refs") or ()
    if isinstance(refs, str):
        try:
            decoded = json.loads(refs)
            refs = decoded if isinstance(decoded, list) else ()
        except json.JSONDecodeError:
            refs = ()
    return LegacyEpisodeRecord(
        legacy_id=str(legacy_id), tenant_id=str(metadata.get("tenant_id") or ""),
        user_id=str(metadata.get("user_id") or ""),
        conversation_id=str(
            metadata.get("conversation_id") or metadata.get("conv_id") or ""
        ),
        source_event_refs=tuple(str(item) for item in refs),
        case_id=str(metadata.get("case_id") or ""),
        case_status=str(metadata.get("case_status") or ""),
        outcome_verification_ref=str(
            metadata.get("outcome_verification_ref") or ""
        ),
        outcome_verification_status=str(
            metadata.get("outcome_verification_status") or ""
        ),
        role=str(metadata.get("role") or ""),
        content_sha256=hashlib.sha256(str(document).encode("utf-8")).hexdigest(),
        deletion_epoch=int(metadata.get("deletion_epoch") or 0),
        tombstoned=bool(metadata.get("tombstoned") or False),
    )
