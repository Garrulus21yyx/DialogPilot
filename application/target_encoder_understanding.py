"""Adapt a Target-native encoder artifact into the turn-understanding cascade."""
from __future__ import annotations

import re

from application.encoder_fast_path import (
    EncoderFastPathPolicy,
    FastPathDecision,
    IntentEncoderOutput,
)
from application.target_encoder_artifact import DEFER_LABEL, TargetTextEncoderArtifact


_IDENTIFIER = re.compile(r"\b[A-Za-z]{1,12}[-_]?\d{2,64}\b")


class TargetEncoderUnderstanding:
    version = "target-encoder-understanding-v1"

    def __init__(self, artifact: TargetTextEncoderArtifact) -> None:
        self._artifact = artifact
        self._policy = EncoderFastPathPolicy(
            skill_thresholds=artifact.manifest.threshold_by_skill,
            max_boundary_score=0.65,
            minimum_margin=0.08,
        )

    async def __call__(self, observations, state, registry):
        self._artifact.validate_registry(registry)
        candidates, defer_score = self._artifact.predict(observations.raw_text)
        top = candidates[0]
        class_by_label = {
            item.label: item for item in self._artifact.manifest.classes
        }
        target = class_by_label[top.candidate_id]
        normalized_text = observations.raw_text.lower()
        if not any(term in normalized_text for term in target.required_signal_terms):
            return FastPathDecision(False, "ENCODER_REQUIRED_SIGNAL_MISSING")
        fields = dict(observations.structured_fields)
        identifiers = _IDENTIFIER.findall(observations.raw_text)
        if target.skill_id == "refund_status_summary" and identifiers:
            fields.setdefault("order_id", identifiers[0])
        if target.skill_id == "general_qa":
            fields["question"] = observations.raw_text
        required = registry.skill(target.skill_id).required_arguments
        missing = tuple(name for name in required if not fields.get(name))
        output = IntentEncoderOutput(
            domains=(type(top)(target.owner_agent, top.score),),
            skills=tuple(
                type(item)(class_by_label[item.candidate_id].skill_id, item.score)
                for item in candidates
            ),
            entities=tuple(fields.items()),
            boundary_score=defer_score,
            multi_intent=False,
            out_of_distribution=(
                defer_score >= max(item.score for item in candidates)
                or top.candidate_id == DEFER_LABEL
            ),
            missing_inputs=missing,
        )
        return self._policy.decide(output, state, registry)
