"""Properties of partial supervision and the closed single-action projection."""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from application.encoder_input import EncoderInput
from application.target_encoder_artifact import DEFER_LABEL
from evaluation.encoder_component_data import annotate, known
from evaluation.encoder_components import (CAPABILITIES, COMPONENTS, encode_targets,
    masked_component_loss, component_routing_scores)


def test_every_binary_component_state_has_exactly_the_declared_projection():
    classes = (DEFER_LABEL, *CAPABILITIES)
    states = np.array(list(itertools.product((0., 1.), repeat=len(COMPONENTS))))
    scores = component_routing_scores(states, COMPONENTS, classes)
    for state, result in zip(states, scores):
        requested, denied, planning = state[:3], state[3:6], state[6]
        allowed = requested.sum() == 1 and not planning and not (requested * denied).any()
        assert bool(result[1:].sum()) == allowed
        assert result[0] == (not allowed)
        if allowed:
            assert np.array_equal(requested, result[1:])


def test_label_order_is_irrelevant():
    values = np.array([[.02, .98, .03, .99, .02, .01, .02]])
    classes = (DEFER_LABEL, *CAPABILITIES)
    expected = dict(zip(classes, component_routing_scores(values, COMPONENTS, classes)[0]))
    rng = np.random.default_rng(17)
    for _ in range(30):
        columns = rng.permutation(len(COMPONENTS))
        output = list(rng.permutation(classes))
        actual = component_routing_scores(values[:, columns], tuple(COMPONENTS[i] for i in columns), output)[0]
        assert dict(zip(output, actual)) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.1, 1.1])
def test_invalid_scores_are_not_silently_accepted(value):
    with pytest.raises(ValueError):
        component_routing_scores([[value] * len(COMPONENTS)], COMPONENTS, (DEFER_LABEL, *CAPABILITIES))


def test_unknown_loss_has_no_gradient_and_known_negatives_do():
    torch = pytest.importorskip("torch")
    logits = torch.zeros((2, len(COMPONENTS)), requires_grad=True)
    labels = torch.tensor([[-1.] * 6 + [1.], [0.] * 6 + [1.]])
    loss = masked_component_loss(SimpleNamespace(logits=logits), labels)
    assert loss.item() == pytest.approx(np.log(2))
    loss.backward()
    assert torch.equal(logits.grad[0, :6], torch.zeros(6))
    assert (logits.grad[1, :6] > 0).all()
    assert (logits.grad[:, 6] < 0).all()


def test_defer_is_not_an_absence_annotation():
    row = dict(label=DEFER_LABEL, source="opaque-legacy", case_id="x")
    targets, basis = annotate(row, {}, {})
    assert basis == "legacy-components-unknown"
    assert encode_targets(targets) == [-1.] * 6 + [1.]


def test_audited_single_affirmative_has_own_polarity_but_not_unrelated_negation_gold():
    for label in CAPABILITIES:
        targets, _ = annotate(dict(label=label, source="agent-authored-synthetic"), {}, {})
        assert targets[f"requested:{label}"] == 1
        assert targets[f"denied:{label}"] == 0
        assert all(targets[f"denied:{k}"] is None for k in CAPABILITIES if k != label)
    with pytest.raises(ValueError, match="source requires"):
        annotate(dict(label=CAPABILITIES[0], source="unreviewed-external"), {}, {})


def test_no_bucket_preserves_withdrawal_correction_and_addition_distinctions():
    seeds = json.loads(Path("data/training/semantic-context-families-v1.json").read_text())
    for language in ("zh", "en"):
        seed = seeds[language]
        for primary, prompt in zip(("refund_status_summary", "product_identification"), seed["families"][0]):
            base = dict(label=DEFER_LABEL, source="agent-authored-counterfactual-dialogue-v1",
                        case_id="x", messages=[{"content": prompt}])
            stop, _ = annotate({**base, "text": seed["no"][0]}, {}, seed)
            assert stop[f"requested:{primary}"] == 0 and stop[f"denied:{primary}"] == 1
            ambiguous, _ = annotate({**base, "text": seed["no"][3]}, {}, seed)
            assert ambiguous[f"requested:{primary}"] is None
            addition, _ = annotate({**base, "text": seed["no"][7]}, {}, seed)
            assert addition[f"requested:{primary}"] == addition["needs_planning"] == 1


def test_generated_corpus_has_provenance_split_isolation_and_combination_holdout():
    root = Path("data/training/encoder-components-v2-reviewed")
    owners, identities = {}, {}
    pair_seen = set()
    for language in ("zh", "en"):
        for split in ("train", "calibration", "heldout"):
            rows = list(map(json.loads, (root / language / f"{split}.jsonl").read_text().splitlines()))
            ids = {r["case_id"] for r in rows}
            for row in rows:
                assert not row["group_id"].startswith("bitext:")
                assert "source_intent" not in row
                encode_targets(row["semantic_targets"])
                assert owners.setdefault(row["group_id"], split) == split
                identity = (language, EncoderInput.from_record(row).identity())
                assert identity not in identities
                identities[identity] = split
                assert set(row.get("source_case_ids", ())) <= ids
                active = {k for k in CAPABILITIES if row["semantic_targets"][f"requested:{k}"] == 1}
                if active == {"general_qa", "product_identification"}:
                    assert split == "heldout"
                    pair_seen.add(language)
    assert pair_seen == {"zh", "en"}
