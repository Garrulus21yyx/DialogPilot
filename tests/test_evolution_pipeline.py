import asyncio
from dataclasses import replace

import pytest

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


def test_intent_candidate_requires_approved_annotation():
    unreviewed = _case(BadCaseStage.INTENT, "intent-payment-security")
    blocked = CreditAttributor().attribute(BadCaseMiner().cluster([unreviewed])[0])
    assert blocked.evolvable is False

    approved = replace(
        unreviewed,
        approved_intent="account_security",
        annotation_id="annotation-1",
        classifier_fingerprint="f" * 64,
        expected_behavior={"intent": "account_security"},
    )
    cluster = BadCaseMiner().cluster([approved])[0]
    attribution = CreditAttributor().attribute(cluster)

    assert attribution.evolvable is True
    assert attribution.allowed_surfaces
    with pytest.raises(BundleContractError, match="approved"):
        asyncio.run(GEPALiteProposalGenerator(FakeProvider()).generate(
            base=AgentBundle(version="intent-v1"),
            cluster=BadCaseMiner().cluster([unreviewed])[0],
            attribution=attribution,
            candidate_count=4,
        ))
    assert cluster.reflection_summary()["approved_intent_examples"] == [{
        "message": "[redacted]",
        "intent": "account_security",
        "annotation_id": "annotation-1",
        "classifier_fingerprint": "f" * 64,
    }]
