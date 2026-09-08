"""Invariant checks for offline family sampling and source ownership."""
from collections import Counter, defaultdict
from types import SimpleNamespace

import pytest

from evaluation.domain_dialogue_dataset import family_indices, validate_batch


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
