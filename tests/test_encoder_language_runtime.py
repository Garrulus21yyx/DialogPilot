import asyncio
from pathlib import Path
import json

import pytest

from infrastructure.target_runtime_composition import _target_encoder
from application.target_encoder_artifact import load_target_text_encoder_artifact
from evaluation.encoder_fastpath_evaluation import evaluate


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("language,expected", [("zh", "zh"), ("zh-CN", "zh"), ("en", "en"), ("en-US", "en")])
def test_language_selects_one_independently_calibrated_artifact(monkeypatch, language, expected):
    monkeypatch.setenv("TARGET_ENCODER_ENABLED", "true")
    monkeypatch.delenv("TARGET_ENCODER_ARTIFACT_DIR", raising=False)
    encoder = _target_encoder(ROOT, language=language)
    assert encoder._artifact.manifest.required_languages == (expected,)
    assert encoder._artifact.manifest.threshold_by_capability


def test_artifact_override_cannot_misrepresent_its_language(monkeypatch):
    monkeypatch.setenv("TARGET_ENCODER_ENABLED", "true")
    monkeypatch.setenv("TARGET_ENCODER_ARTIFACT_DIR", str(ROOT / "artifacts/target-encoder-zh-context-v2"))
    with pytest.raises(RuntimeError, match="calibrated for the selected language"):
        _target_encoder(ROOT, language="en")


def test_unsupported_language_does_not_silently_load_chinese(monkeypatch):
    monkeypatch.setenv("TARGET_ENCODER_ENABLED", "true")
    with pytest.raises(RuntimeError, match="zh or en"):
        _target_encoder(ROOT, language="fr")


def test_independent_challenge_keeps_no_false_accepts_and_saves_calls():
    rows = [json.loads(line) for line in (ROOT / "data/eval/encoder-language-independent-2026-09-08.jsonl").read_text().splitlines()]
    result = asyncio.run(evaluate(rows, {lang: ROOT / f"artifacts/target-encoder-{lang}-context-v2" for lang in ("zh", "en")}))
    for language in ("zh", "en"):
        summary = result["summary"][language]
        assert summary["false_accepts"] == []
        assert 0 < summary["accepted"] < summary["total"]
        assert summary["planner_off"] - summary["planner_on"] == summary["accepted"]


def test_language_data_split_groups_and_inputs_are_disjoint():
    from application.encoder_input import EncoderInput
    for language in ("zh", "en"):
        previous_groups, previous_inputs = set(), set()
        for split in ("train", "calibration", "heldout"):
            rows = [json.loads(line) for line in (ROOT / f"data/training/encoder-language-v3/{language}/{split}.jsonl").read_text().splitlines()]
            groups = {row["group_id"] for row in rows}
            inputs = {EncoderInput.from_record(row).identity() for row in rows}
            assert not previous_groups.intersection(groups)
            assert not previous_inputs.intersection(inputs)
            assert len(inputs) == len(rows)
            assert len({row["case_id"] for row in rows}) == len(rows)
            previous_groups.update(groups)
            previous_inputs.update(inputs)


def test_checked_in_artifacts_are_bound_to_actual_data():
    import hashlib
    for language in ("zh", "en"):
        artifact = load_target_text_encoder_artifact(ROOT / f"artifacts/target-encoder-{language}-context-v2")
        for split, digest in artifact.manifest.dataset_sha256:
            path = ROOT / f"data/training/encoder-language-v3/{language}/{split}.jsonl"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
