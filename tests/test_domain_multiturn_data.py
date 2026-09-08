import itertools
import json
from pathlib import Path

import pytest

from application.domain_encoder import DOMAINS, DEFER_DOMAIN
from application.encoder_input import EncoderInput
from evaluation.domain_multiturn_data import active_domain, additions


@pytest.mark.parametrize("old,new", itertools.product(DOMAINS, repeat=2))
def test_relation_labels_depend_on_current_effective_goals(old, new):
    assert active_domain("continuation", old) == old
    assert active_domain("replacement", old, new) == new
    assert active_domain("withdrawal", old) == DEFER_DOMAIN
    assert active_domain("unresolved_reference", old) == DEFER_DOMAIN
    assert active_domain("addition", old, new) == (old if old == new else DEFER_DOMAIN)


def test_generated_context_is_role_alternating_and_same_short_answer_depends_on_history():
    seed = json.loads(Path("data/training/domain-multiturn-seeds-v2.json").read_text())
    identities, task_splits, expression_splits = set(), {}, {}
    for language in ("zh", "en"):
        short_answers = {}
        for split in ("train", "calibration", "heldout"):
            for tasks in seed["scenarios"][language][split].values():
                for task in tasks:
                    assert task_splits.setdefault((language, task), split) == split
            for forms in seed["expressions"][language][split].values():
                for form in forms:
                    assert expression_splits.setdefault((language, form), split) == split
            rows = list(additions(seed, language, split))
            assert {len(r["messages"]) for r in rows} == {2, 4, 6}
            for row in rows:
                assert [m["role"] for m in row["messages"]] == ["user", "assistant"] * (len(row["messages"]) // 2)
                identity = EncoderInput.from_record(row).identity()
                assert identity not in identities
                identities.add(identity)
                if row["relation"] == "continuation":
                    short_answers.setdefault(row["text"], set()).add(row["label"])
            assert all(owners == set(DOMAINS) for owners in short_answers.values())


def test_unreviewed_relation_is_not_silently_assigned_a_domain():
    with pytest.raises(ValueError, match="unknown conversation relation"):
        active_domain("new_relation", "general")


def test_v2_preserves_v1_and_keeps_regression_out_of_training():
    regression = {EncoderInput.from_record(json.loads(line)).identity()
                  for line in Path("data/eval/domain-encoder-independent-2026-09-08.jsonl").read_text().splitlines()}
    for language in ("zh", "en"):
        seen, groups = set(), {}
        for split in ("train", "calibration", "heldout"):
            base = list(map(json.loads, Path(f"data/training/domain-encoder-v1/{language}/{split}.jsonl").read_text().splitlines()))
            rows = list(map(json.loads, Path(f"data/training/domain-encoder-v2/{language}/{split}.jsonl").read_text().splitlines()))
            assert rows[:len(base)] == base
            for row in rows:
                identity = EncoderInput.from_record(row).identity()
                assert identity not in seen and identity not in regression
                seen.add(identity)
                assert groups.setdefault(row["group_id"], split) == split
