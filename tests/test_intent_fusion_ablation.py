import json
from collections import Counter
from pathlib import Path

import pytest

from core.intent_recognizer import IntentCategory
from evaluation.intent_fusion_ablation import (
    FusionConfig,
    build_cases,
    load_reviewed_candidate_cases,
    load_external_intent_cases,
    replay_fusion,
    sealed_validation_report,
)


def source(intent, confidence, **extra):
    return {"intent": intent.value, "confidence": confidence, **extra}


def test_replay_matches_current_three_way_agreement():
    sources = {
        "llm": source(IntentCategory.QUERY, 1.0),
        "ngram": source(IntentCategory.QUERY, 1.0),
        "semantic": source(IntentCategory.OTHER, 0.0),
        "pattern": source(IntentCategory.OTHER, 0.0),
    }
    intent, score, _ = replay_fusion(
        sources, FusionConfig("current", 0.7, 0.2, 0.1, "ngram")
    )
    assert intent == "query"
    assert score == pytest.approx(0.9)


def test_replay_preserves_specific_pattern_refinement_contract():
    sources = {
        "llm": source(IntentCategory.BILLING, 0.8),
        "ngram": source(IntentCategory.OTHER, 0.0),
        "semantic": source(IntentCategory.OTHER, 0.0),
        "pattern": source(IntentCategory.REFUND, 0.5),
    }
    intent, score, evidence = replay_fusion(
        sources, FusionConfig("current", 0.7, 0.2, 0.1, "ngram")
    )
    assert intent == "refund"
    assert score == pytest.approx(0.56)
    assert evidence["refined_by_pattern"] == 0.5


def test_replay_can_ablate_pattern_refinement():
    sources = {
        "llm": source(IntentCategory.BILLING, 0.8),
        "ngram": source(IntentCategory.OTHER, 0.0),
        "semantic": source(IntentCategory.OTHER, 0.0),
        "pattern": source(IntentCategory.REFUND, 0.5),
    }
    intent, _, evidence = replay_fusion(
        sources,
        FusionConfig("no-refine", 0.7, 0.2, 0.1, "ngram", refine_by_pattern=False),
    )
    assert intent == "billing"
    assert "refined_by_pattern" not in evidence


def test_replay_llm_failure_uses_embedding_before_pattern():
    sources = {
        "llm": source(IntentCategory.OTHER, 0.0, failed=True),
        "ngram": source(IntentCategory.QUERY, 0.2),
        "semantic": source(IntentCategory.OTHER, 0.0),
        "pattern": source(IntentCategory.REFUND, 1.0),
    }
    intent, score, _ = replay_fusion(
        sources, FusionConfig("current", 0.7, 0.2, 0.1, "ngram")
    )
    assert intent == "query"
    assert score == pytest.approx(0.2)


def test_compact_suite_has_bounded_group_safe_slices():
    root = Path(__file__).resolve().parents[1]
    cases = build_cases(root / "data/eval/dialogpilot-500-v1/cases.jsonl")
    counts = Counter(case["slice"] for case in cases)
    assert counts == {
        "business_boundary": 50,
        "rejection": 50,
        "conflict": 50,
        "semantic_similarity": 52,
    }
    splits_by_group = {}
    for case in cases:
        splits_by_group.setdefault(case["source_group_id"], set()).add(case["analysis_split"])
    assert all(len(splits) == 1 for splits in splits_by_group.values())
    assert all(case["source_review"].get("status") != "human_reviewed" for case in cases)


def _reviewed_candidate(index: int, slice_name: str) -> dict:
    return {
        "schema_version": 1,
        "id": f"fresh-{index:03d}",
        "layer": "intent",
        "slice": slice_name,
        "group_id": f"group-{index:03d}",
        "input": {"message": f"unique message {index}", "history": []},
        "expected": {"intent": "other", "secondary_intents": []},
        "review": {
            "status": "independently_reviewed_synthetic",
            "notes": "must not enter the inference representation",
        },
        "tags": ["synthetic"],
    }


def test_reviewed_candidate_loader_enforces_contract_and_excludes_notes(tmp_path):
    slices = ["business_boundary", "rejection", "conflict", "semantic_similarity"]
    rows = [_reviewed_candidate(index, slices[(index - 1) // 25]) for index in range(1, 101)]
    rows[70]["input"]["history"] = [{"role": "user", "content": "prior turn"}]
    path = tmp_path / "candidates.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    cases = load_reviewed_candidate_cases(path)

    assert len(cases) == 100
    assert cases[70]["history"] == [{"role": "user", "content": "prior turn"}]
    assert all(case["analysis_split"] == "sealed_validation" for case in cases)
    assert all("source_review" not in case for case in cases)
    assert "notes" not in json.dumps(cases)


def test_reviewed_candidate_loader_rejects_unreviewed_rows(tmp_path):
    slices = ["business_boundary", "rejection", "conflict", "semantic_similarity"]
    rows = [_reviewed_candidate(index, slices[(index - 1) // 25]) for index in range(1, 101)]
    rows[0]["review"]["status"] = "synthetic_pending_human_review"
    path = tmp_path / "candidates.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="not independently reviewed"):
        load_reviewed_candidate_cases(path)


def test_sealed_report_has_no_tuning_selection():
    cases = [{
        "case_id": "one", "slice": "rejection", "analysis_split": "sealed_validation",
        "message": "hello", "history": [], "expected": "other",
    }]
    outputs = {"one": {
        "llm": source(IntentCategory.OTHER, 0.9),
        "ngram": source(IntentCategory.OTHER, 0.0),
        "semantic": {
            "intent": "query", "confidence": 0.6, "second_intent": "request",
            "second_confidence": 0.59, "margin": 0.01,
        },
        "pattern": source(IntentCategory.OTHER, 0.0),
        "pattern_evidence": [],
    }}

    report = sealed_validation_report(
        cases, outputs, semantic_model="test-model", candidate_sha256="abc"
    )

    assert report["status"] == "independently_reviewed_synthetic_sealed_validation"
    assert report["policy_selection"].startswith("frozen before scoring")
    assert "selected_on_dev" not in report
    assert "v2_selected_on_dev" not in report


def test_external_loader_preserves_upstream_split_and_excludes_review_notes(tmp_path):
    from evaluation.dataset import write_dataset

    rows = []
    for split in ("dev", "heldout"):
        rows.append({
            "schema_version": 1,
            "id": f"case-{split}",
            "layer": "intent",
            "split": split,
            "group_id": f"group-{split}",
            "input": {"message": f"message {split}"},
            "expected": {"intent": "other"},
            "tags": ["external"],
            "source": {
                "dataset": "test-source", "license": "CC-BY-4.0",
                "original_label": "oos",
            },
            "review": {"status": "auto_mapped", "notes": "scoring only"},
        })
    write_dataset(
        tmp_path,
        manifest={"dataset_id": "external-test", "version": "1"},
        cases=rows,
    )

    cases = load_external_intent_cases(tmp_path, split="dev")

    assert [case["case_id"] for case in cases] == ["case-dev"]
    assert cases[0]["analysis_split"] == "dev"
    assert "notes" not in json.dumps(cases)
