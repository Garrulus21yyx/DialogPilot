"""Coverage supervision/algebra, not a substitute for fresh semantic evaluation."""
import itertools
import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from application.target_encoder_artifact import DEFER_LABEL
from evaluation.encoder_coverage import hypotheses, pair_targets, routing_scores
from evaluation.target_encoder_training import TARGETS


def test_whole_request_challenge_is_frozen_and_separate():
    from application.encoder_input import EncoderInput
    root = Path(__file__).resolve().parents[1]
    path = root / "data/eval/encoder-whole-request-challenge-2026-09-08.jsonl"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "1500c1737f18bf4e58f6ce8b19a354271821eb7127495694851bd1936406eeef"
    rows = list(map(json.loads, path.read_text().splitlines()))
    assert len(rows) == len({r["case_id"] for r in rows}) == 160
    identities = {EncoderInput.from_record(r).identity() for r in rows}
    assert len(identities) == 160
    consumed = {EncoderInput.from_record(json.loads(line)).identity()
        for p in (root / "data/training/semantic-encoder-v2-final").glob("*/*.jsonl")
        for line in p.read_text().splitlines()}
    assert not identities & consumed


@pytest.mark.parametrize("language", ["zh", "en"])
def test_pair_targets_cover_existing_scope_and_defer(language):
    labels = tuple(hypotheses(language))
    assert set(labels) == set(TARGETS)
    for gold in [*labels, DEFER_LABEL]:
        expected = pair_targets(gold, labels)
        assert sum(expected) == (gold != DEFER_LABEL)
        for index, label in enumerate(labels):
            assert expected[index] == (label == gold)
    with pytest.raises(ValueError, match="unknown"):
        pair_targets("unregistered", labels)


def test_pair_scores_are_not_competing_category_probabilities():
    labels = tuple(hypotheses("en"))
    classes = (DEFER_LABEL, *labels)
    scores = np.array([[.01, .02, .03], [.99, .98, .01], [.01, .99, .01]])
    result = routing_scores(scores, labels, classes)
    assert np.allclose(result[[0, 2], 1:], scores[[0, 2]])
    assert result[0].argmax() == 0  # All insufficient => DEFER.
    assert result[1].argmax() == 0  # Multiple binary COMPLETE decisions => DEFER.
    assert result[2].argmax() == 2


def test_candidate_and_class_reordering_preserves_scores():
    labels = tuple(hypotheses("zh"))
    values = {label: value for label, value in zip(labels, [.01, .8, .2])}
    for order in itertools.permutations(labels):
        for classes in itertools.permutations((DEFER_LABEL, *labels)):
            row = routing_scores([[values[label] for label in order]], order, classes)[0]
            assert dict(zip(classes, row)) == {**values, DEFER_LABEL: 1 - .8}


@pytest.mark.parametrize("scores", [[.5, .1, .1], [.5, .5, .1], [.49, .1, .2]])
def test_binary_ties_and_all_negative_remain_deferred(scores):
    labels = tuple(hypotheses("en"))
    row = routing_scores([scores], labels, (DEFER_LABEL, *labels))[0]
    # TargetEncoderUnderstanding uses boundary >= largest candidate for OOD.
    assert row[0] >= max(row[1:])


@pytest.mark.parametrize("scores", [[[float("nan"), 0, 0]], [[-1, 0, 0]], [[2, 0, 0]], [[0, 0]]])
def test_invalid_pair_scores_rejected(scores):
    labels = tuple(hypotheses("en"))
    with pytest.raises(ValueError):
        routing_scores(scores, labels, (DEFER_LABEL, *labels))


def test_pair_inference_batches_once_and_overflow_does_not_call_model():
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    pytest.importorskip("datasets")
    from types import SimpleNamespace
    from application.encoder_input import EncoderInput
    from evaluation.semantic_encoder_experiment import SemanticScorer

    class Tokenizer:
        length = 12
        def __call__(self, texts, **kwargs):
            assert len(texts) == len(kwargs["text_pair"]) == 3
            assert len(set(texts)) == 1
            assert kwargs["truncation"] is False
            return {"input_ids": torch.zeros((3, self.length), dtype=torch.long)}

    class Model:
        calls = 0
        def __call__(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(logits=torch.tensor([[5., 0.], [0., 5.], [5., 0.]]))

    scorer = SemanticScorer.__new__(SemanticScorer)
    scorer.contracts = hypotheses("en")
    scorer.targets = tuple(SimpleNamespace(label=label) for label in scorer.contracts)
    scorer.max_tokens = 384
    scorer.device = torch.device("cpu")
    scorer.tokenizer = Tokenizer()
    scorer.model = Model()
    ranked, boundary = scorer.predict(EncoderInput("yes", (("assistant", "Identify the picture?"),)))
    assert scorer.model.calls == 1
    assert ranked[0].candidate_id == tuple(scorer.contracts)[1]
    assert ranked[0].score > .99 and boundary < .01
    scorer.tokenizer.length = 385
    ranked, boundary = scorer.predict(EncoderInput("long input"))
    assert scorer.model.calls == 1
    assert boundary == 1 and all(row.score == 0 for row in ranked)


def test_coverage_algebra_through_real_fast_path_policy():
    from types import SimpleNamespace
    from application.encoder_fast_path import RankedCandidate
    from evaluation.legacy_capability_encoder import TargetEncoderUnderstanding
    from evaluation.encoder_fastpath_evaluation import prepare

    labels = tuple(hypotheses("en"))
    targets = [SimpleNamespace(label=label, owner_agent=owner,
        required_arguments=arguments, capability_ref=f"{kind}:{capability}")
        for label, (owner, kind, capability, arguments) in TARGETS.items()]
    manifest = SimpleNamespace(classes=targets,
        threshold_by_capability={c.capability_ref: .9 for c in targets},
        required_arguments_by_capability={c.capability_ref: c.required_arguments for c in targets})
    obs, state, _, registry, context = prepare({"case_id": "algebra", "text": "fixture"})

    class Artifact:
        input_schema = "dialogpilot-encoder-context-v1"
        def validate_registry(self, value):
            assert value is registry
        def predict(self, value):
            row = routing_scores([self.scores], labels, (DEFER_LABEL, *labels))[0]
            return tuple(sorted((RankedCandidate(label, float(score)) for label, score
                                 in zip(labels, row[1:])), key=lambda r: -r.score)), float(row[0])

    artifact = Artifact()
    artifact.manifest = manifest
    encoder = TargetEncoderUnderstanding(artifact)
    for scores in itertools.product((0., .5, .99), repeat=3):
        artifact.scores = scores
        decision = asyncio.run(encoder(obs, state, registry, context))
        expected = sum(s >= .5 for s in scores) == 1 and max(scores) == .99
        assert decision.accepted == expected, (scores, decision.reason_code)
        if expected:
            command, = decision.proposal.commands
            label = labels[scores.index(.99)]
            assert (command.tool_id or command.skill_id) == TARGETS[label][2]


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_calibration_uses_and_records_requested_device(tmp_path, monkeypatch, device):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    pytest.importorskip("datasets")
    from types import SimpleNamespace
    from application.encoder_input import EncoderInput
    from application.encoder_fast_path import RankedCandidate
    from evaluation import semantic_encoder_experiment as experiment
    trained = tmp_path / "trained"
    trained.mkdir()
    output = tmp_path / "calibrated"

    class Scorer:
        contracts = {"a": "capability"}
        objective = "whole-request-pair-v1"
        def __init__(self, path, *, device):
            assert path == trained
            self.device = device
        def predict(self, value):
            return (RankedCandidate("a", .9),), .1

    monkeypatch.setattr(experiment, "SemanticScorer", Scorer)
    monkeypatch.setattr(experiment, "_load", lambda path: [SimpleNamespace(input=EncoderInput("fixture"))])
    monkeypatch.setattr(experiment, "calibrate", lambda *args, **kwargs: {"classes": []})
    experiment.finalize_candidate(tmp_path, trained, output, "en", device=device)
    assert json.loads((output / "manifest.json").read_text())["calibration_device"] == device
