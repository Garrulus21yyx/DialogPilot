"""候选 Bundle 的真实 Gate 证据、Graduation 与 Pareto 选择。"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from evaluation.graduation import (
    GraduationDecision,
    GraduationEvidence,
    GraduationGate,
)
from services.evolution.bundle import AgentBundle


@dataclass(frozen=True)
class GateArtifact:
    """一个 Gate Runner 的不可伪装执行证据。"""

    name: str
    passed: bool
    evidence_id: str
    details: Mapping[str, Any]
    checksum: str

    @classmethod
    def create(cls, name: str, passed: bool, evidence_id: str, details: Mapping[str, Any]):
        payload = json.dumps(dict(details), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return cls(
            name=str(name),
            passed=bool(passed),
            evidence_id=str(evidence_id),
            details=dict(details),
            checksum=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )


GateRunner = Callable[[AgentBundle], GateArtifact | Awaitable[GateArtifact]]


@dataclass(frozen=True)
class CandidateExternalEvidence:
    review_status: str
    fresh_heldout: bool
    heldout_evidence_id: str
    heldout_checksum: str
    latency_ratio: float = 1.0
    cost_ratio: float = 1.0


@dataclass(frozen=True)
class CandidateRun:
    bundle: AgentBundle
    report: Any
    gates: Tuple[GateArtifact, ...]
    decision: GraduationDecision

    @property
    def objectives(self) -> Tuple[float, float, float]:
        return (
            float(getattr(self.report, "pass_rate", 0.0)),
            -float(self.decision.metrics.get("latency_ratio", 1.0)),
            -float(self.decision.metrics.get("cost_ratio", 1.0)),
        )


class CandidateRunner:
    """运行候选评测和真实 Gate；不接受调用者直接提交 hard_gates 布尔值。"""

    def __init__(self, evaluator: Any, gate_runners: Mapping[str, GateRunner]):
        self._evaluator = evaluator
        self._gate_runners = dict(gate_runners)
        self._graduation = GraduationGate()

    async def run(
        self,
        bundle: AgentBundle,
        *,
        intent_cases: Optional[list] = None,
        dialog_cases: Optional[list] = None,
        external: CandidateExternalEvidence,
    ) -> CandidateRun:
        report = await self._evaluator.run(
            intent_cases=intent_cases,
            dialog_cases=dialog_cases,
            metadata={"source": "candidate_runner"},
            agent_bundle=bundle,
        )
        artifacts = []
        for name, runner in sorted(self._gate_runners.items()):
            artifact = runner(bundle)
            if inspect.isawaitable(artifact):
                artifact = await artifact
            if not isinstance(artifact, GateArtifact) or artifact.name != name:
                raise TypeError(f"gate runner {name} returned invalid provenance")
            if not artifact.evidence_id or len(artifact.checksum) != 64:
                raise ValueError(f"gate runner {name} returned incomplete provenance")
            artifacts.append(artifact)
        decision = self._graduation.evaluate(GraduationEvidence(
            candidate_id=bundle.version,
            report=report,
            hard_gates={artifact.name: artifact.passed for artifact in artifacts},
            review_status=external.review_status,
            fresh_heldout=external.fresh_heldout,
            heldout_evidence_id=external.heldout_evidence_id,
            heldout_checksum=external.heldout_checksum,
            latency_ratio=external.latency_ratio,
            cost_ratio=external.cost_ratio,
        ))
        return CandidateRun(bundle, report, tuple(artifacts), decision)

    @staticmethod
    def pareto_front(runs: Iterable[CandidateRun]) -> Tuple[CandidateRun, ...]:
        eligible = [run for run in runs if run.decision.graduated]
        front = []
        for candidate in eligible:
            dominated = any(
                all(a >= b for a, b in zip(other.objectives, candidate.objectives))
                and any(a > b for a, b in zip(other.objectives, candidate.objectives))
                for other in eligible if other is not candidate
            )
            if not dominated:
                front.append(candidate)
        return tuple(sorted(front, key=lambda item: item.bundle.version))
