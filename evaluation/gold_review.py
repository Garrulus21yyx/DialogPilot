"""M6-T03 privacy-reviewed dual annotation and fresh-heldout contracts."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


class GoldReviewError(ValueError):
    pass


GROUP_DIMENSIONS = ("user", "order", "product", "time", "semantic")


def policy_contract() -> Mapping[str, Any]:
    return {
        "policy_id": "m6-t03-gold-review-policy-v1",
        "required_group_dimensions": list(GROUP_DIMENSIONS),
        "required_reviewers": 2,
        "disagreement": "INDEPENDENT_ARBITRATION_REQUIRED",
        "direct_contact_identifiers": "REJECT",
        "supported_slices": [
            "invalid", "oos", "security", "compound", "multi_turn",
            "handoff", "multimodal", "standard",
        ],
        "fix_participation_transition": "FRESH_HELDOUT_TO_CONSUMED_REGRESSION",
        "confidence_method": "wilson-95",
        "gold_promotion_owner": "Evaluation + Product/Support Ops + Privacy",
    }


@dataclass(frozen=True)
class ReviewCandidate:
    candidate_id: str
    sanitized_input: Mapping[str, Any]
    source_refs: tuple[str, ...]
    group_hashes: Mapping[str, str]
    slices: tuple[str, ...]
    privacy_review_id: str

    def __post_init__(self) -> None:
        if not self.candidate_id.strip() or not self.privacy_review_id.strip():
            raise GoldReviewError("candidate and privacy review identity are required")
        if not self.source_refs:
            raise GoldReviewError("candidate requires opaque source refs")
        if set(self.group_hashes) != set(GROUP_DIMENSIONS) or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(value))
            for value in self.group_hashes.values()
        ):
            raise GoldReviewError("all group dimensions require SHA-256 identities")
        text = json.dumps(self.sanitized_input, ensure_ascii=False, sort_keys=True)
        if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text) or re.search(
            r"(?<!\d)(?:\+?\d[\s-]?){8,15}(?!\d)", text,
        ):
            raise GoldReviewError("candidate contains direct contact identifier")
        allowed_slices = {
            "invalid", "oos", "security", "compound", "multi_turn",
            "handoff", "multimodal", "standard",
        }
        if not self.slices or set(self.slices) - allowed_slices:
            raise GoldReviewError("candidate slices are empty or unsupported")

    @property
    def group_fingerprint(self) -> str:
        return _sha256(self.group_hashes)


@dataclass(frozen=True)
class BlindAnnotation:
    candidate_id: str
    reviewer_id: str
    labels: Mapping[str, Any]
    submitted_at: str
    notes: str = ""

    def __post_init__(self) -> None:
        if not all((self.candidate_id.strip(), self.reviewer_id.strip(), self.submitted_at.strip())):
            raise GoldReviewError("annotation identity and timestamp are required")
        if not self.labels:
            raise GoldReviewError("annotation labels are required")


@dataclass(frozen=True)
class Arbitration:
    candidate_id: str
    arbitrator_id: str
    labels: Mapping[str, Any]
    decided_at: str
    rationale: str


@dataclass(frozen=True)
class ReviewDecision:
    candidate_id: str
    status: str
    labels: Mapping[str, Any]
    reviewer_ids: tuple[str, str]
    decided_at: str
    arbitration: Arbitration | None = None
    consumed_regression: bool = False
    fix_commits: tuple[str, ...] = ()

    def consume_for_fix(self, commit: str) -> "ReviewDecision":
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise GoldReviewError("consumed regression requires full fix commit")
        return ReviewDecision(
            self.candidate_id, self.status, self.labels, self.reviewer_ids,
            self.decided_at, self.arbitration, True,
            tuple(dict.fromkeys((*self.fix_commits, commit))),
        )


def decide_review(
    first: BlindAnnotation,
    second: BlindAnnotation,
    *,
    arbitration: Arbitration | None = None,
) -> ReviewDecision:
    if first.candidate_id != second.candidate_id:
        raise GoldReviewError("annotations refer to different candidates")
    if first.reviewer_id == second.reviewer_id:
        raise GoldReviewError("dual review requires distinct reviewers")
    reviewers = (first.reviewer_id, second.reviewer_id)
    if _canonical(first.labels) == _canonical(second.labels):
        if arbitration is not None:
            raise GoldReviewError("agreed annotations must not fabricate arbitration")
        return ReviewDecision(
            first.candidate_id, "AGREED", dict(first.labels), reviewers,
            max(first.submitted_at, second.submitted_at),
        )
    if arbitration is None:
        raise GoldReviewError("annotation disagreement requires arbitration")
    if arbitration.candidate_id != first.candidate_id:
        raise GoldReviewError("arbitration candidate mismatch")
    if arbitration.arbitrator_id in reviewers or not all((
        arbitration.arbitrator_id.strip(), arbitration.decided_at.strip(),
        arbitration.rationale.strip(), bool(arbitration.labels),
    )):
        raise GoldReviewError("arbitration requires an independent auditable decision")
    return ReviewDecision(
        first.candidate_id, "ARBITRATED", dict(arbitration.labels), reviewers,
        arbitration.decided_at, arbitration,
    )


def assign_group_safe_splits(
    candidates: Sequence[ReviewCandidate],
    *,
    heldout_percent: int,
    split_salt: str,
) -> Mapping[str, str]:
    if not 1 <= heldout_percent <= 50 or not split_salt.strip():
        raise GoldReviewError("invalid heldout percentage or split salt")
    group_split: dict[str, str] = {}
    result = {}
    for candidate in candidates:
        group = candidate.group_fingerprint
        bucket = int(hashlib.sha256(f"{split_salt}:{group}".encode()).hexdigest()[:8], 16) % 100
        split = "heldout" if bucket < heldout_percent else "dev"
        prior = group_split.setdefault(group, split)
        if prior != split:
            raise GoldReviewError("group split is not deterministic")
        result[candidate.candidate_id] = prior
    return result


def cohens_kappa(
    first: Sequence[BlindAnnotation], second: Sequence[BlindAnnotation], *, label: str,
) -> float:
    left = {item.candidate_id: item.labels.get(label) for item in first}
    right = {item.candidate_id: item.labels.get(label) for item in second}
    if set(left) != set(right) or not left:
        raise GoldReviewError("kappa requires paired non-empty annotations")
    ids = sorted(left)
    observed = sum(left[item] == right[item] for item in ids) / len(ids)
    left_counts, right_counts = Counter(left.values()), Counter(right.values())
    labels = set(left_counts) | set(right_counts)
    expected = sum(
        left_counts[item] / len(ids) * right_counts[item] / len(ids) for item in labels
    )
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else 0.0
    return (observed - expected) / (1.0 - expected)


def build_review_manifest(
    candidates: Sequence[ReviewCandidate],
    decisions: Sequence[ReviewDecision],
    splits: Mapping[str, str],
) -> Mapping[str, Any]:
    by_candidate = {item.candidate_id: item for item in decisions}
    if set(by_candidate) != {item.candidate_id for item in candidates}:
        raise GoldReviewError("every candidate requires exactly one review decision")
    if len(by_candidate) != len(decisions):
        raise GoldReviewError("duplicate review decision")
    if set(splits) != set(by_candidate):
        raise GoldReviewError("split assignment does not cover reviewed candidates")
    rows = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        decision = by_candidate[candidate.candidate_id]
        rows.append({
            "candidate_id": candidate.candidate_id,
            "group_fingerprint": candidate.group_fingerprint,
            "split": splits[candidate.candidate_id],
            "slices": sorted(candidate.slices),
            "privacy_review_id": candidate.privacy_review_id,
            "review_status": "human_reviewed",
            "decision_status": decision.status,
            "reviewer_ids": list(decision.reviewer_ids),
            "arbitrator_id": (
                decision.arbitration.arbitrator_id if decision.arbitration else None
            ),
            "consumed_regression": decision.consumed_regression,
            "fix_commits": list(decision.fix_commits),
            "labels_sha256": _sha256(decision.labels),
        })
    return {
        "schema_version": "gold-review-manifest-v1",
        "case_count": len(rows),
        "fresh_heldout_count": sum(
            row["split"] == "heldout" and not row["consumed_regression"] for row in rows
        ),
        "consumed_regression_count": sum(row["consumed_regression"] for row in rows),
        "rows": rows,
        "rows_sha256": _sha256(rows),
    }


def error_slice_report(
    candidates: Sequence[ReviewCandidate], outcomes: Mapping[str, bool],
) -> Mapping[str, Any]:
    if set(outcomes) != {item.candidate_id for item in candidates}:
        raise GoldReviewError("outcomes must cover the reviewed candidate set")
    by_slice: dict[str, list[bool]] = {}
    for candidate in candidates:
        for slice_name in candidate.slices:
            by_slice.setdefault(slice_name, []).append(bool(outcomes[candidate.candidate_id]))

    def summarize(values: Sequence[bool]) -> Mapping[str, Any]:
        successes, total = sum(values), len(values)
        low, high = _wilson_interval(successes, total)
        return {
            "count": total,
            "passed": successes,
            "rate": successes / total,
            "confidence_method": "wilson-95",
            "ci95": [low, high],
        }

    return {
        "overall": summarize(tuple(outcomes.values())),
        "by_slice": {
            key: summarize(value) for key, value in sorted(by_slice.items())
        },
    }


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total < 1 or not 0 <= successes <= total:
        raise GoldReviewError("invalid confidence interval counts")
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()
