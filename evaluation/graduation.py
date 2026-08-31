"""不可变评测基线与候选晋级门禁。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple


class GraduationStatus(str, Enum):
    GRADUATED = "graduated"
    REJECTED = "rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class GraduationPolicy:
    """质量、数据和效率门禁；安全硬门禁永远不能被平均分抵消。"""

    min_pass_rate: float = 0.75
    required_hard_gates: Tuple[str, ...] = (
        "security",
        "identity_isolation",
        "tool_authorization",
        "coverage",
        "stateful",
    )
    require_fresh_heldout: bool = True
    allowed_review_statuses: Tuple[str, ...] = ("human_reviewed",)
    max_latency_ratio: float = 1.20
    max_cost_ratio: float = 1.20


@dataclass(frozen=True)
class GraduationEvidence:
    """一次候选晋级所需的完整、显式证据。"""

    candidate_id: str
    report: Any
    hard_gates: Mapping[str, bool]
    review_status: str
    fresh_heldout: bool
    heldout_evidence_id: str = ""
    heldout_checksum: str = ""
    latency_ratio: float = 1.0
    cost_ratio: float = 1.0


@dataclass(frozen=True)
class GraduationDecision:
    status: GraduationStatus
    candidate_id: str
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def graduated(self) -> bool:
        return self.status is GraduationStatus.GRADUATED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "candidate_id": self.candidate_id,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


class GraduationGate:
    """把 EvalReport 与外部硬门禁收敛为一个不可歧义的晋级结论。"""

    def __init__(self, policy: Optional[GraduationPolicy] = None):
        self.policy = policy or GraduationPolicy()

    def evaluate(self, evidence: GraduationEvidence) -> GraduationDecision:
        report = evidence.report
        reasons = []
        report_bundle = str(getattr(report, "metadata", {}).get("agent_bundle_version") or "")
        if report_bundle and report_bundle != evidence.candidate_id:
            reasons.append(
                f"report bundle {report_bundle!r} does not match candidate {evidence.candidate_id!r}"
            )
        missing = [
            gate for gate in self.policy.required_hard_gates
            if gate not in evidence.hard_gates
        ]
        failed = [gate for gate, passed in evidence.hard_gates.items() if not passed]
        if failed:
            reasons.append(f"failed hard gates: {failed}")
        judge_failures = sum(
            bool(getattr(result, "metadata", {}).get("judge_failed"))
            for result in getattr(report, "results", [])
        )
        regressions = list(getattr(report, "regressions", []) or [])
        pass_rate = float(getattr(report, "pass_rate", 0.0))
        if judge_failures:
            reasons.append(f"judge failures: {judge_failures}")
        if regressions:
            reasons.append(f"reported regressions: {regressions}")
        if pass_rate < self.policy.min_pass_rate:
            reasons.append(
                f"pass_rate={pass_rate:.4f} below {self.policy.min_pass_rate:.4f}"
            )
        if evidence.latency_ratio > self.policy.max_latency_ratio:
            reasons.append(
                f"latency_ratio={evidence.latency_ratio:.4f} exceeds {self.policy.max_latency_ratio:.4f}"
            )
        if evidence.cost_ratio > self.policy.max_cost_ratio:
            reasons.append(
                f"cost_ratio={evidence.cost_ratio:.4f} exceeds {self.policy.max_cost_ratio:.4f}"
            )

        insufficient = []
        if missing:
            insufficient.append(f"missing hard gates: {missing}")
        if self.policy.require_fresh_heldout and not evidence.fresh_heldout:
            insufficient.append("fresh heldout evidence is required")
        if evidence.fresh_heldout and not evidence.heldout_evidence_id.strip():
            insufficient.append("fresh heldout evidence id is required")
        checksum = evidence.heldout_checksum.strip().lower()
        if evidence.fresh_heldout and (
            len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum)
        ):
            insufficient.append("fresh heldout checksum must be a SHA-256 digest")
        if evidence.review_status not in self.policy.allowed_review_statuses:
            insufficient.append(
                f"review_status={evidence.review_status!r} is not allowed"
            )
        metrics = {
            "pass_rate": pass_rate,
            "judge_failures": judge_failures,
            "latency_ratio": evidence.latency_ratio,
            "cost_ratio": evidence.cost_ratio,
            "hard_gates": dict(evidence.hard_gates),
            "review_status": evidence.review_status,
            "fresh_heldout": evidence.fresh_heldout,
            "heldout_evidence_id": evidence.heldout_evidence_id,
            "heldout_checksum": checksum,
        }
        if reasons:
            return GraduationDecision(
                GraduationStatus.REJECTED, evidence.candidate_id, tuple(reasons + insufficient), metrics
            )
        if insufficient:
            return GraduationDecision(
                GraduationStatus.INSUFFICIENT_EVIDENCE,
                evidence.candidate_id,
                tuple(insufficient),
                metrics,
            )
        return GraduationDecision(
            GraduationStatus.GRADUATED, evidence.candidate_id, (), metrics
        )


@dataclass(frozen=True)
class BaselineSnapshot:
    snapshot_id: str
    candidate_id: str
    promoted_at: str
    promoted_by: str
    report: Dict[str, Any]
    decision: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ImmutableBaselineStore:
    """保存不可变 Snapshot，并只通过显式 promote 原子切换 Active 指针。"""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def active(self) -> Optional[BaselineSnapshot]:
        data = self._load()
        active_id = str(data.get("active_snapshot_id") or "")
        for raw in data.get("snapshots", []):
            if raw.get("snapshot_id") == active_id:
                return BaselineSnapshot(**raw)
        return None

    def promote(
        self,
        *,
        report: Any,
        decision: GraduationDecision,
        actor: str,
    ) -> BaselineSnapshot:
        if not decision.graduated:
            raise ValueError("only a graduated candidate can become the active baseline")
        actor = str(actor or "").strip()
        if not actor:
            raise ValueError("baseline promotion requires an actor")
        report_dict = self._to_dict(report)
        canonical = json.dumps(
            {
                "candidate_id": decision.candidate_id,
                "report": report_dict,
                "decision": decision.to_dict(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot = BaselineSnapshot(
            snapshot_id=snapshot_id,
            candidate_id=decision.candidate_id,
            promoted_at=datetime.now(timezone.utc).isoformat(),
            promoted_by=actor[:200],
            report=report_dict,
            decision=decision.to_dict(),
        )
        data = self._load()
        known = {item["snapshot_id"]: item for item in data.get("snapshots", [])}
        known.setdefault(snapshot_id, snapshot.to_dict())
        final = {
            "schema_version": self.SCHEMA_VERSION,
            "active_snapshot_id": snapshot_id,
            "snapshots": list(known.values()),
        }
        self._atomic_write(final)
        return BaselineSnapshot(**known[snapshot_id])

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": self.SCHEMA_VERSION, "active_snapshot_id": "", "snapshots": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version") == self.SCHEMA_VERSION and "snapshots" in data:
            return data
        # 旧版 baseline.json 是单个 EvalReport。迁移为只读 legacy snapshot，
        # 但不会因为一次普通 eval run 被覆盖。
        report = dict(data)
        canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        snapshot_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        legacy = BaselineSnapshot(
            snapshot_id=snapshot_id,
            candidate_id="legacy-baseline",
            promoted_at=str(report.get("timestamp") or ""),
            promoted_by="legacy-migration",
            report=report,
            decision={"status": "legacy", "candidate_id": "legacy-baseline"},
        )
        return {
            "schema_version": self.SCHEMA_VERSION,
            "active_snapshot_id": snapshot_id,
            "snapshots": [legacy.to_dict()],
        }

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _to_dict(value: Any) -> Dict[str, Any]:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, Mapping):
            return dict(value)
        raise TypeError("baseline report must be a dataclass or mapping")
