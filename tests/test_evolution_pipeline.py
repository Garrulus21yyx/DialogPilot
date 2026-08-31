import asyncio
from types import SimpleNamespace

import pytest

from evaluation.candidate_runner import (
    CandidateExternalEvidence,
    CandidateRunner,
    GateArtifact,
)
from evaluation.graduation import GraduationEvidence, GraduationGate
from services.badcase_registry import (
    BadCase,
    BadCaseSeverity,
    BadCaseStage,
    BadCaseStatus,
)
from services.evolution import (
    AgentBundle,
    BadCaseMiner,
    BundleContractError,
    CreditAttributor,
    GEPALiteProposalGenerator,
)


def _case(stage=BadCaseStage.ROUTING, group="route-refund"):
    return BadCase(
        badcase_id=f"case-{stage.value}", fingerprint="f" * 64,
        semantic_group_id=group, source="test", stage=stage,
        severity=BadCaseSeverity.P1, status=BadCaseStatus.REPRODUCED,
        symptom_code="wrong_owner", trace_id="trace", request_id="request",
        user_ref="user-ref", sanitized_input="[redacted]", published_response="",
        evidence={}, versions={}, eval_layer="routing",
        expected_behavior={"owners": ["billing"], "task_ids": ["billing_task"]},
        reproduction={}, root_cause="support threshold too permissive",
        owner_module="task_planner", linked_case_id="", fixed_by_commit="",
        occurrence_count=3, created_at="1", first_seen_at="1",
        last_seen_at="2", updated_at="2",
    )


class FakeProvider:
    async def propose(self, payload, candidate_count):
        assert payload["allowed_surfaces"] == ["routing_policy"]
        return [
            {"routing_policy": {"supporting_threshold": value}}
            for value in (0.5, 0.55, 0.6, 0.65)
        ]


def test_gepa_lite_only_changes_attributed_surface():
    cluster = BadCaseMiner().cluster([_case()])[0]
    attribution = CreditAttributor().attribute(cluster)
    base = AgentBundle(
        version="agent-v1",
        routing_policy={"supporting_threshold": 0.45, "clarification_threshold": 0.5},
        retrieval_policy={"top_k": 3, "rrf_k": 60, "vector_weight": 0.0, "lexical_weight": 1.0},
    )
    candidates = asyncio.run(GEPALiteProposalGenerator(FakeProvider()).generate(
        base=base, cluster=cluster, attribution=attribution, candidate_count=4,
    ))
    assert len(candidates) == 4
    assert all(candidate.base_version == "agent-v1" for candidate in candidates)
    assert all(candidate.retrieval_policy == base.retrieval_policy for candidate in candidates)
    assert all(candidate.source_badcase_groups == ("route-refund",) for candidate in candidates)


def test_security_badcase_cannot_generate_bundle():
    cluster = BadCaseMiner().cluster([_case(BadCaseStage.INPUT_SECURITY, "injection")])[0]
    attribution = CreditAttributor().attribute(cluster)
    assert attribution.evolvable is False
    with pytest.raises(BundleContractError, match="not auto-evolvable"):
        asyncio.run(GEPALiteProposalGenerator(FakeProvider()).generate(
            base=AgentBundle(version="agent-v1"),
            cluster=cluster,
            attribution=attribution,
            candidate_count=4,
        ))


class FakeEvaluator:
    def __init__(self):
        self.bundle = None

    async def run(self, **kwargs):
        self.bundle = kwargs["agent_bundle"]
        return SimpleNamespace(
            pass_rate=0.95,
            regressions=[],
            results=[],
            metadata={"agent_bundle_version": self.bundle.version},
        )


def _gate(name, passed=True):
    def runner(bundle):
        return GateArtifact.create(
            name, passed, f"{bundle.version}:{name}", {"executed": 10, "passed": passed},
        )
    return runner


def test_candidate_runner_uses_provenance_gates_and_rejects_safety_failure():
    evaluator = FakeEvaluator()
    runners = {
        name: _gate(name, passed=name != "security")
        for name in ("security", "identity_isolation", "tool_authorization", "coverage", "stateful")
    }
    candidate = AgentBundle(version="candidate-v1")
    result = asyncio.run(CandidateRunner(evaluator, runners).run(
        candidate,
        external=CandidateExternalEvidence(
            review_status="human_reviewed",
            fresh_heldout=True,
            heldout_evidence_id="fresh-v1",
            heldout_checksum="a" * 64,
        ),
    ))
    assert evaluator.bundle is candidate
    assert result.decision.graduated is False
    assert "failed hard gates" in " ".join(result.decision.reasons)
    assert all(artifact.evidence_id for artifact in result.gates)
    assert CandidateRunner.pareto_front([result]) == ()


def test_candidate_report_cannot_be_relabelled_as_another_bundle():
    evaluator = FakeEvaluator()
    runners = {
        name: _gate(name)
        for name in ("security", "identity_isolation", "tool_authorization", "coverage", "stateful")
    }
    candidate = AgentBundle(version="candidate-v1")
    result = asyncio.run(CandidateRunner(evaluator, runners).run(
        candidate,
        external=CandidateExternalEvidence(
            review_status="human_reviewed", fresh_heldout=True,
            heldout_evidence_id="fresh-v1", heldout_checksum="b" * 64,
        ),
    ))
    assert result.decision.graduated is True
    relabelled = GraduationGate().evaluate(GraduationEvidence(
        candidate_id="other",
        report=result.report,
        hard_gates={artifact.name: artifact.passed for artifact in result.gates},
        review_status="human_reviewed",
        fresh_heldout=True,
        heldout_evidence_id="fresh-v1",
        heldout_checksum="b" * 64,
    ))
    assert relabelled.graduated is False
    assert "does not match candidate" in " ".join(relabelled.reasons)
