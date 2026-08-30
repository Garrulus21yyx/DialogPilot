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
from scripts.build_eval_dataset import _case, build_bitext
from scripts.review_eval_dataset import mark_human_reviewed


REPO_DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "dialogpilot-v1"


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

    assert summary["case_count"] == 28
    assert summary["corpus_count"] == 6
    assert summary["by_layer"] == {
        "intent": 8,
        "retrieval": 6,
        "routing": 7,
        "stateful": 7,
    }
    assert bundle.select(gold_only=True) == []
    assert len(bundle.select(split="heldout")) == 9


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


def test_missing_predictions_fail_instead_of_shrinking_denominator(tmp_path):
    bundle = write_dataset(
        tmp_path,
        manifest=manifest(),
        cases=[case("i1", "intent", "heldout", {"message": "hello"}, {"intent": "greeting"})],
    )

    with pytest.raises(PredictionError, match="missing predictions"):
        score_bundle(bundle, [], split="heldout")


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
