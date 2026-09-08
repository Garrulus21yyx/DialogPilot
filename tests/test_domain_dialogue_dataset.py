"""Invariant checks for offline family sampling and source ownership."""
from collections import Counter, defaultdict
from types import SimpleNamespace

import pytest

from evaluation.domain_dialogue_dataset import family_indices, validate_batch, reviewed_source_rows
from evaluation.domain_dialogue_teacher import RUBRIC_VERSION
from evaluation.domain_dialogue_reannotation import ANNOTATION_VERSION


@pytest.mark.parametrize("seed", range(20))
def test_sampling_preserves_class_mass_and_balances_families(seed):
    rows = [SimpleNamespace(label=label, group_id=f"{label}:{group}")
            for label in ("general", "order_logistics", "__DEFER__")
            for group, size in enumerate((1, 3, 9, 25)) for _ in range(size)]
    indices = family_indices(rows, seed)
    assert indices == family_indices(rows, seed)
    assert Counter(rows[i].label for i in indices) == Counter(r.label for r in rows)
    counts = defaultdict(Counter)
    for i in indices:
        counts[rows[i].label][rows[i].group_id] += 1
    for families in counts.values():
        assert len(families) == 4
        assert max(families.values()) - min(families.values()) <= 1


@pytest.mark.parametrize("language", ("zh", "en"))
@pytest.mark.parametrize("index", range(24))
def test_batch_provenance_is_bound_to_language_and_split(language, index):
    split = "train" if index < 16 else "calibration" if index < 20 else "heldout"
    record = dict(batch_id=f"domain-v3:{language}:{index}", language=language,
                  split=split, status="reviewed")
    validate_batch(record, language, index)
    for field, value in (("language", "other"), ("split", "other"), ("batch_id", "other"),
                         ("status", "pending")):
        with pytest.raises(ValueError):
            validate_batch({**record, field: value}, language, index)


@pytest.mark.parametrize("field,value", [
    ("source_sha256", "stale"), ("batch_id", "other"), ("language", "en"),
    ("split", "heldout"), ("rubric_version", "old"), ("annotation_version", "old"), ("status", "failed")])
def test_new_labels_cannot_fall_back_to_old_or_cross_source(field, value):
    original = dict(batch_id="domain-v3:zh:1", language="zh", split="train")
    review = dict(**original, source_sha256="current", rubric_version=RUBRIC_VERSION,
                  annotation_version=ANNOTATION_VERSION,
                  status="reviewed_candidate")
    with pytest.raises(ValueError, match="provenance"):
        reviewed_source_rows(original, {**review, field: value}, "current")
