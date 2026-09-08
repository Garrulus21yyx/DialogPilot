"""Contract checks independent of model quality; acceptance lives in frozen evaluations."""
import json
from pathlib import Path

import numpy as np
import pytest

from application.encoder_input import EncoderInput
from evaluation.target_encoder_training import _load, _select_threshold


ROOT = Path(__file__).resolve().parents[1]


def test_default_fast_accept_is_contained_until_fresh_validation_passes(monkeypatch):
    from infrastructure.target_runtime_composition import _target_encoder
    monkeypatch.delenv("TARGET_ENCODER_ENABLED", raising=False)
    assert _target_encoder(ROOT) is None


@pytest.mark.parametrize("language", ["zh", "en"])
def test_semantic_training_family_and_input_isolation(language):
    seen_inputs, seen_groups, seen_ids = set(), set(), set()
    for split in ("train", "calibration", "heldout"):
        examples = _load(ROOT / f"data/training/semantic-encoder-v2-final/{language}/{split}.jsonl")
        inputs = {r.input.identity() for r in examples}
        groups = {r.group_id for r in examples}
        ids = {r.case_id for r in examples}
        assert len(inputs) == len(examples)
        assert not inputs & seen_inputs
        assert not groups & seen_groups
        assert not ids & seen_ids
        assert all(r.language == language for r in examples)
        seen_inputs |= inputs
        seen_groups |= groups
        seen_ids |= ids
    for path in [ROOT / "data/eval/semantic-encoder-final-challenge-2026-09-08.jsonl"]:
        rows = [r for r in map(json.loads, path.read_text().splitlines()) if r["language"] == language]
        assert not {EncoderInput.from_record(r).identity() for r in rows} & seen_inputs


def test_calibration_empirical_gate_can_only_restrict_acceptance():
    classes = ("a", "b")
    probabilities = np.array([[.95, .05]] * 100 + [[.7, .3]] * 3)
    expected = tuple(["a"] * 100 + ["b"] * 3)
    original = _select_threshold("a", classes, probabilities, expected, .88)
    stricter = _select_threshold("a", classes, probabilities, expected, .88,
                                 minimum_empirical_precision=.98)
    assert original["accepted"] == 103
    assert stricter["accepted"] == 100
    assert stricter["threshold"] >= original["threshold"]


def test_ordered_render_distinguishes_current_history_and_negation():
    pytest.importorskip("transformers")
    pytest.importorskip("datasets")
    pytest.importorskip("torch")
    from evaluation.semantic_encoder_experiment import render
    a = EncoderInput("yes", (("assistant", "Check refund status?"),))
    b = EncoderInput("yes", (("assistant", "Submit refund?"),))
    c = EncoderInput("no", a.messages)
    d = EncoderInput("Check refund status?", (("assistant", "yes"),))
    assert len({render(v) for v in (a,b,c,d)}) == 4
    assert json.loads(render(a))["current_user"] == a.text


def test_independent_family_variants_preserve_declared_labels():
    rows = list(map(json.loads, (ROOT / "data/eval/semantic-encoder-final-challenge-2026-09-08.jsonl").read_text().splitlines()))
    families = {}
    for row in rows:
        family = row.get("family_id", row["case_id"])
        assert families.setdefault(family, row["label"]) == row["label"]
    for lang in ("zh", "en"):
        subset = [r for r in rows if r["language"] == lang]
        assert len(subset) >= 80
        assert sum(bool(r.get("messages")) and r["label"] in {"refund_status_summary", "product_identification"}
                   for r in subset) >= 24
        assert sum(r["label"] == "__DEFER__" for r in subset) >= 30
