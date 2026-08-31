"""版本化评测集、切分、provenance 与分层指标的不变量。"""
import json
from pathlib import Path

import pytest

from evaluation.benchmark import PredictionError, score_bundle
from evaluation.dataset import (
    DatasetBundle,
    DatasetValidationError,
    discover_datasets,
    load_registered_dataset,
    write_dataset,
)
from agents.agent_orchestrator import AgentOrchestrator, Request
from agents.orchestration_contracts import AgentType
from core.intent_recognizer import IntentCategory
from scripts.build_eval_dataset import _case, build_bitext
from scripts.build_intent_weight_verification_dataset import _identity
from scripts.build_project_eval_500 import EXPECTED_DISTRIBUTION
from scripts.review_eval_dataset import mark_human_reviewed


REPO_DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "dialogpilot-v1"
PROJECT_500_DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "dialogpilot-500-v1"


def source():
    return {"dataset": "test", "license": "project-private"}


def review():
    return {
        "status": "human_reviewed",
        "reviewer": "test",
        "reviewed_at": "2026-08-30T00:00:00+00:00",
        "notes": "test fixture",
    }


def case(case_id, layer, split, input_data, expected, *, group_id=None):
    return {
        "schema_version": 1,
        "id": case_id,
        "layer": layer,
        "split": split,
        "group_id": group_id or case_id,
        "input": input_data,
        "expected": expected,
        "tags": [],
        "source": source(),
        "review": review(),
    }


def manifest():
    return {
        "dataset_id": "test-layered",
        "version": "1.0.0",
        "status": "test",
        "sources": [source()],
    }


def test_committed_seed_is_valid_but_not_misreported_as_gold():
    bundle = DatasetBundle.load(REPO_DATASET)
    summary = bundle.summary()

    assert summary["case_count"] == 29
    assert summary["corpus_count"] == 6
    assert summary["by_layer"] == {
        "intent": 8,
        "retrieval": 6,
        "routing": 8,
        "stateful": 7,
    }
    assert bundle.select(gold_only=True) == []
    assert len(bundle.select(split="heldout")) == 9


def test_committed_500_dataset_enforces_all_layer_and_split_contracts():
    bundle = DatasetBundle.load(PROJECT_500_DATASET)
    summary = bundle.summary()

    assert summary["case_count"] == 500
    assert summary["corpus_count"] == 25
    assert summary["by_layer"] == EXPECTED_DISTRIBUTION["by_layer"]
    assert summary["by_split"] == EXPECTED_DISTRIBUTION["by_split"]
    assert summary["by_review_status"] == {
        "auto_mapped": 180,
        "human_reviewed": 0,
        "provisional": 320,
    }
    assert len(bundle.select(layer="stateful", split="heldout")) == 20


def test_500_dataset_stateful_cases_are_executable_protocols():
    bundle = DatasetBundle.load(PROJECT_500_DATASET)
    stateful = bundle.select(layer="stateful")

    assert len(stateful) == 100
    assert {case.input["scenario"]["category"] for case in stateful} == {
        "memory", "react-security",
    }
    assert all(case.input["scenario"]["setup"] for case in stateful)
    assert all(case.input["scenario"]["action"] for case in stateful)
    assert all(set(case.expected["assertions"].values()) == {True} for case in stateful)


def test_all_120_routing_labels_match_current_task_plan_contract():
    bundle = DatasetBundle.load(PROJECT_500_DATASET)
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {agent_type: [object()] for agent_type in AgentType}

    for eval_case in bundle.select(layer="routing"):
        request = Request(
            message=eval_case.input["message"],
            user_id="eval-user",
            conv_id=eval_case.case_id,
            intent=IntentCategory(eval_case.input["intent"]),
            intent_confidence=eval_case.input["intent_confidence"],
            entities=eval_case.input.get("entities", {}),
        )
        plan = orchestrator._build_task_plan(request)
        assert [owner.value for owner in plan.agent_types] == eval_case.expected["owners"]
        assert [task.task_id for task in plan.ordered_tasks] == eval_case.expected["task_ids"]


def test_checksum_detects_unversioned_case_mutation(tmp_path):
    dataset = write_dataset(
        tmp_path,
        manifest=manifest(),
        cases=[case("i1", "intent", "dev", {"message": "hello"}, {"intent": "greeting"})],
    )
    cases_path = dataset.root / "cases.jsonl"
    cases_path.write_text(cases_path.read_text() + "\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="checksum mismatch"):
        DatasetBundle.load(tmp_path)


def test_group_variants_cannot_cross_dev_and_heldout(tmp_path):
    cases = [
        case("i1", "intent", "dev", {"message": "hello"}, {"intent": "greeting"}, group_id="same"),
        case("i2", "intent", "heldout", {"message": "hi"}, {"intent": "greeting"}, group_id="same"),
    ]

    with pytest.raises(DatasetValidationError, match="crosses dev/heldout"):
        write_dataset(tmp_path, manifest=manifest(), cases=cases)


def test_manifest_distribution_is_an_enforced_contract(tmp_path):
    declared = manifest()
    declared["expected_distribution"] = {"by_layer": {"intent": 2}}

    with pytest.raises(DatasetValidationError, match="expected_distribution.by_layer"):
        write_dataset(
            tmp_path,
            manifest=declared,
            cases=[case("i1", "intent", "dev", {"message": "hello"}, {"intent": "greeting"})],
        )


def test_duplicate_corpus_ids_are_rejected_at_dataset_boundary(tmp_path):
    with pytest.raises(DatasetValidationError, match="duplicate corpus ids"):
        write_dataset(
            tmp_path,
            manifest=manifest(),
            cases=[case("r1", "retrieval", "dev", {"query": "hello"}, {"relevant_ids": ["doc"]})],
            corpus=[{"id": "doc", "content": "one"}, {"id": "doc", "content": "two"}],
        )


def test_dataset_registry_only_accepts_direct_child_ids(tmp_path):
    registry = tmp_path / "registry"
    write_dataset(
        registry / "safe-v1",
        manifest=manifest(),
        cases=[case("i1", "intent", "dev", {"message": "hello"}, {"intent": "greeting"})],
    )

    assert load_registered_dataset(registry, "safe-v1").summary()["case_count"] == 1
    with pytest.raises(DatasetValidationError, match="invalid dataset_id"):
        load_registered_dataset(registry, "../safe-v1")


def test_dataset_discovery_exposes_version_and_review_state(tmp_path):
    registry = tmp_path / "registry"
    write_dataset(
        registry / "safe-v1",
        manifest=manifest(),
        cases=[case("i1", "intent", "dev", {"message": "hello"}, {"intent": "greeting"})],
    )

    discovered = discover_datasets(registry)

    assert discovered[0]["registry_id"] == "safe-v1"
    assert discovered[0]["valid"] is True
    assert discovered[0]["by_review_status"]["human_reviewed"] == 1


def test_human_review_status_requires_auditable_metadata(tmp_path):
    invalid = case("i1", "intent", "dev", {"message": "hello"}, {"intent": "greeting"})
    invalid["review"] = {"status": "human_reviewed", "reviewer": "test"}

    with pytest.raises(DatasetValidationError, match="human_reviewed requires"):
        write_dataset(tmp_path, manifest=manifest(), cases=[invalid])


def test_review_command_promotes_selected_case_and_recomputes_checksum(tmp_path):
    draft = case("i1", "intent", "dev", {"message": "hello"}, {"intent": "greeting"})
    draft["review"] = {"status": "provisional", "reviewer": None}
    bundle = write_dataset(tmp_path, manifest=manifest(), cases=[draft])
    old_checksum = bundle.manifest["cases_sha256"]
    cases_path = tmp_path / "cases.jsonl"
    edited = json.loads(cases_path.read_text(encoding="utf-8"))
    edited["input"]["message"] = "hello after ambiguity review"
    cases_path.write_text(json.dumps(edited, ensure_ascii=False) + "\n", encoding="utf-8")

    reviewed = mark_human_reviewed(
        tmp_path,
        case_ids=["i1"],
        reviewer="reviewer-a",
        notes="intent and ambiguity checked",
        reviewed_at="2026-08-30T12:00:00+00:00",
    )

    assert reviewed.manifest["cases_sha256"] != old_checksum
    assert reviewed.select(gold_only=True)[0].input["message"] == "hello after ambiguity review"
    assert reviewed.select(gold_only=True)[0].review == {
        "status": "human_reviewed",
        "reviewer": "reviewer-a",
        "reviewed_at": "2026-08-30T12:00:00+00:00",
        "notes": "intent and ambiguity checked",
    }


def test_layered_scorer_reports_deterministic_process_and_result_metrics(tmp_path):
    cases = [
        case("intent-1", "intent", "heldout", {"message": "weather"}, {"intent": "other"}),
        case(
            "route-1", "routing", "heldout", {"message": "401 and charged"},
            {"owners": ["technical", "billing"], "task_ids": ["technical_task", "billing_task"]},
        ),
        case(
            "rag-1", "retrieval", "heldout", {"query": "E401"},
            {"relevant_ids": ["doc-1", "doc-2"]},
        ),
        case(
            "state-1", "stateful", "heldout", {"message": "forged approval"},
            {"assertions": {"blocked": True, "side_effect_zero": True}},
        ),
    ]
    bundle = write_dataset(
        tmp_path,
        manifest=manifest(),
        cases=cases,
        corpus=[{"id": "doc-1", "content": "401"}, {"id": "doc-2", "content": "login"}],
    )
    predictions = [
        {"case_id": "intent-1", "actual": {"intent": "other"}},
        {"case_id": "route-1", "actual": {
            "owners": ["technical", "billing"],
            "task_ids": ["technical_task", "billing_task"],
        }},
        {"case_id": "rag-1", "actual": {"retrieved_ids": ["noise", "doc-1", "doc-2"]}},
        {"case_id": "state-1", "actual": {"assertions": {
            "blocked": True, "side_effect_zero": True,
        }}},
    ]

    report = score_bundle(bundle, predictions, split="heldout", retrieval_k=3)

    assert report["case_count"] == 4
    assert report["dataset_checksum"] == bundle.manifest["cases_sha256"]
    assert report["layers"]["intent"]["accuracy"] == 1.0
    assert report["layers"]["intent"]["oos_recall"] == 1.0
    assert report["layers"]["routing"]["owner_exact_match"] == 1.0
    assert report["layers"]["retrieval"]["recall_at_3"] == 1.0
    assert report["layers"]["retrieval"]["mrr"] == 0.5
    assert report["layers"]["stateful"]["all_assertions_pass"] == 1.0


def test_routing_scorer_accepts_typed_no_worker_policy_terminal(tmp_path):
    """空 Owner/Task 是策略终态的正确结果，不能被 fan-out 指标反向判错。"""
    bundle = write_dataset(
        tmp_path,
        manifest=manifest(),
        cases=[case(
            "route-oos", "routing", "heldout", {"message": "weather"},
            {"owners": [], "task_ids": [], "disposition": "out_of_scope"},
        )],
    )
    report = score_bundle(
        bundle,
        [{"case_id": "route-oos", "actual": {
            "owners": [], "task_ids": [], "disposition": "out_of_scope",
        }}],
        split="heldout",
    )

    assert report["pass_rate"] == 1.0
    assert report["layers"]["routing"]["fanout_efficiency"] == 1.0
    assert report["layers"]["routing"]["disposition_exact_match"] == 1.0


def test_missing_predictions_fail_instead_of_shrinking_denominator(tmp_path):
    bundle = write_dataset(
        tmp_path,
        manifest=manifest(),
        cases=[case("i1", "intent", "heldout", {"message": "hello"}, {"intent": "greeting"})],
    )

    with pytest.raises(PredictionError, match="missing predictions"):
        score_bundle(bundle, [], split="heldout")


def test_layer_filter_allows_independent_prediction_files(tmp_path):
    bundle = write_dataset(
        tmp_path,
        manifest=manifest(),
        cases=[
            case("i1", "intent", "dev", {"message": "hello"}, {"intent": "greeting"}),
            case("s1", "stateful", "dev", {"message": "secure"}, {"assertions": {"blocked": True}}),
        ],
    )

    report = score_bundle(
        bundle,
        [{"case_id": "s1", "actual": {"assertions": {"blocked": True}}}],
        split="dev",
        gold_only=False,
        layers={"stateful"},
    )

    assert report["case_count"] == 1
    assert report["layers_requested"] == ["stateful"]
    assert set(report["layers"]) == {"stateful"}


def test_external_case_preserves_original_label_license_and_upstream_split():
    mapped = _case(
        source_name="banking77",
        license_name="CC-BY-4.0",
        source_url="https://example.test",
        source_split="test",
        source_label="transaction_charged_twice",
        message="charged twice",
        mapped_intent="payment_issue",
    )

    assert mapped["split"] == "heldout"
    assert mapped["source"]["original_label"] == "transaction_charged_twice"
    assert mapped["source"]["license"] == "CC-BY-4.0"
    assert mapped["review"]["status"] == "auto_mapped"


def test_bitext_requires_explicit_cdla_sharing_acceptance():
    with pytest.raises(SystemExit, match="CDLA-Sharing-1.0"):
        build_bitext(1, accept_cdla_sharing=False)


def test_external_identity_binds_dataset_split_label_and_exact_message():
    source_row = {
        "dataset": "banking77",
        "original_split": "test",
        "original_label": "request_refund",
    }

    assert _identity(source_row, "refund please") == (
        "banking77", "test", "request_refund", "refund please"
    )
    assert _identity(source_row, "refund please") != _identity(source_row, "different")
