"""M6-T01 Dataset/Rubric v2 service-chain hard-gate properties."""
import json
from pathlib import Path

import pytest

from evaluation.service_chain import (
    ServiceChainActual,
    ServiceChainCase,
    ServiceChainContractError,
    ServiceChainDataset,
    ServiceLayer,
    score_service_chain,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/eval/service-chain-v2-contract"


def test_v2_contract_dataset_freezes_all_service_layers_without_claiming_gold():
    dataset = ServiceChainDataset.load(DATASET)

    assert len(dataset.cases) == 3
    assert set(dataset.manifest["service_layers"]) == {
        layer.value for layer in ServiceLayer
    }
    assert dataset.manifest["review_status"] == "PROVISIONAL_NOT_GOLD"
    assert all(case.review["status"] == "provisional" for case in dataset.cases)
    assert all(case.backend_state for case in dataset.cases)


def test_tool_authority_receipt_parameter_and_publication_contract_passes():
    case = ServiceChainDataset.load(DATASET).cases[1]
    actual = ServiceChainActual(
        layers={
            "route_mode": {"owner": "Application", "mode": "TOOL_ONLY"},
            "tool_authority": {
                "owner": "Domain Tool", "authority": "CustomerOperations",
            },
            "tool_effect": {
                "owner": "CustomerOperations", "effect_status": "none",
            },
            "publication": {"owner": "Application", "publisher_count": 1},
        },
        tool_calls=({
            "name": "refund_status",
            "parameters": {"order_id": "A-1", "include_history": False},
            "receipt": {
                "authority": "CustomerOperations",
                "receipt_schema_version": "refund-status-receipt-v1",
            },
        },),
        publication_ids=("response-one",),
        safety_flags={"unauthorized_tool": False, "duplicate_effect": False},
    )

    score = score_service_chain(case, actual)

    assert score.passed is True
    assert score.deterministic_pass is True
    assert score.violations == ()


def test_semantic_scores_cannot_cover_missing_receipt_or_duplicate_publication():
    case = ServiceChainDataset.load(DATASET).cases[1]
    actual = ServiceChainActual(
        layers={
            "route_mode": {"owner": "Application", "mode": "TOOL_ONLY"},
            "tool_authority": {
                "owner": "Domain Tool", "authority": "CustomerOperations",
            },
            "tool_effect": {
                "owner": "CustomerOperations", "effect_status": "none",
            },
            "publication": {"owner": "Application", "publisher_count": 1},
        },
        tool_calls=({
            "name": "refund_status", "parameters": {"order_id": "A-1"},
            "receipt": {"authority": "CustomerOperations"},
        },),
        publication_ids=("response-one", "response-one"),
        semantic_scores={
            "faithfulness": 1.0, "answer_relevancy": 1.0,
            "context_precision": 1.0, "context_recall": 1.0,
        },
        semantic_scorer="ragas-compatible-contract-v1",
    )

    score = score_service_chain(case, actual)

    assert score.passed is False
    assert score.deterministic_pass is False
    assert score.checks["tool.receipt.refund_status.receipt_schema_version"] is False
    assert score.checks["publication.maximum"] is False


def test_claim_evidence_and_fixed_semantic_scorer_are_both_required():
    case = ServiceChainDataset.load(DATASET).cases[0]
    layers = {
        "route_mode": {"owner": "Application", "mode": "KNOWLEDGE_ONLY"},
        "retrieval": {"owner": "KnowledgeRetriever", "status": "OK"},
        "generation_claims": {"owner": "CoverageGate", "covered": True},
        "publication": {"owner": "Application", "publisher_count": 1},
    }
    wrong = score_service_chain(case, ServiceChainActual(
        layers=layers,
        claims={"settlement-time": ()},
        publication_ids=("response-one",),
        semantic_scores={key: 1.0 for key in case.rubric.semantic_minimums},
        semantic_scorer="different-scorer-v9",
    ))
    assert wrong.passed is False
    assert wrong.checks["claim.settlement-time.evidence"] is False
    assert wrong.checks["semantic.scorer_version"] is False

    correct = score_service_chain(case, ServiceChainActual(
        layers=layers,
        claims={"settlement-time": ("refund-policy-v1#settlement",)},
        publication_ids=("response-one",),
        semantic_scores={key: 1.0 for key in case.rubric.semantic_minimums},
        semantic_scorer=case.rubric.semantic_scorer,
        safety_flags={
            "cross_tenant_evidence": False,
            "stale_evidence_reuse": False,
            "unnecessary_vlm": False,
        },
    ))
    assert correct.passed is True


def test_unknown_observation_layer_and_unknown_rubric_field_fail_closed():
    case = ServiceChainDataset.load(DATASET).cases[2]
    score = score_service_chain(case, ServiceChainActual(
        layers={"future_magic": {"owner": "unknown"}},
    ))
    assert score.passed is False
    assert score.checks["layers.closed"] is False

    raw = json.loads((DATASET / "cases.jsonl").read_text(encoding="utf-8").splitlines()[0])
    raw["expected"]["deterministic_rubric"]["judge_overrides_safety"] = True
    with pytest.raises(ServiceChainContractError, match="unknown rubric fields"):
        ServiceChainCase.from_mapping(raw)


def test_multi_agent_structure_budget_and_handoff_are_deterministic():
    case = ServiceChainDataset.load(DATASET).cases[2]
    layers = {
        "route_mode": {"owner": "Application", "mode": "HANDOFF"},
        "tool_authority": {
            "owner": "AuthorityPolicyRegistry", "supported": False,
        },
        "handoff": {
            "owner": "Product/Support Ops", "release_status": "draft_only",
        },
        "publication": {"owner": "Application", "publisher_count": 1},
    }
    actual = ServiceChainActual(
        layers=layers,
        handoff={
            "reason_code": "AUTHORITY_UNSUPPORTED", "risk_level": "high",
            "missing_inputs": ["verified account owner"],
            "release_status": "draft_only",
        },
        publication_ids=("handoff-draft-one",),
        task_owners={"security_task": "security", "billing_task": "billing"},
        parallel_waves=(("billing_task", "security_task"),),
        budget_outcome="within_budget",
        safety_flags={"unauthorized_tool": False, "duplicate_publication": False},
    )

    score = score_service_chain(case, actual)

    assert score.passed is True
    assert case.decision_gold["domain_owner"] == "security"
    assert case.decision_gold["supporting_agents"] == ["billing"]

    missing_owner = score_service_chain(case, ServiceChainActual(
        **{**actual.__dict__, "task_owners": {"security_task": "security"}},
    ))
    assert missing_owner.passed is False
    assert missing_owner.checks["task.owner.billing_task"] is False


def test_authoritative_backend_state_is_mandatory():
    raw = json.loads((DATASET / "cases.jsonl").read_text(encoding="utf-8").splitlines()[0])
    raw["expected"]["authoritative_backend_state"] = {}

    with pytest.raises(ServiceChainContractError, match="backend state is required"):
        ServiceChainCase.from_mapping(raw)
