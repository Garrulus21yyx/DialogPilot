"""Adapt a Target-native encoder artifact into the turn-understanding cascade."""
from __future__ import annotations

from application.encoder_fast_path import (
    EncoderFastPathPolicy,
    IntentEncoderOutput,
    FastPathDecision,
)
from application.target_encoder_artifact import DEFER_LABEL, TargetTextEncoderArtifact
from application.encoder_input import EncoderInput


class TargetEncoderUnderstanding:
    version = "target-encoder-understanding-v1"

    def __init__(self, artifact: TargetTextEncoderArtifact) -> None:
        self._artifact = artifact
        self._policy = EncoderFastPathPolicy(
            capability_thresholds=artifact.manifest.threshold_by_capability,
            required_arguments=artifact.manifest.required_arguments_by_capability,
            max_boundary_score=0.65,
            minimum_margin=0.08,
        )

    async def __call__(self, observations, state, registry, turn_context=None):
        self._artifact.validate_registry(registry)
        if turn_context is not None and turn_context.summary is not None:
            return FastPathDecision(False, "ENCODER_SUMMARY_UNCALIBRATED")
        value = EncoderInput.from_turn(observations, state, turn_context)
        if self._artifact.input_schema == "text-v1" and (value.messages or value.objectives):
            return FastPathDecision(False, "ENCODER_CONTEXT_UNCALIBRATED")
        candidates, defer_score = self._artifact.predict(value)
        top = candidates[0]
        class_by_label = {
            item.label: item for item in self._artifact.manifest.classes
        }
        target = class_by_label[top.candidate_id]
        # A category prediction cannot resolve an elliptical search query. The
        # existing planner owns query authorship; never send bare "yes" to RAG.
        if value.messages and {"query", "question"}.intersection(target.required_arguments):
            return FastPathDecision(False, "ENCODER_QUERY_AUTHORING_REQUIRED")
        fields = dict(observations.structured_fields)
        bindings = turn_context.entity_bindings
        for name in target.required_arguments:
            selected = bindings.resolve(name, state).selected
            if selected is not None:
                fields.setdefault(name, selected.value)
        if "query" in target.required_arguments:
            fields["query"] = observations.raw_text
        if "question" in target.required_arguments:
            fields["question"] = observations.raw_text
        missing = tuple(
            name for name in target.required_arguments if not fields.get(name)
        )
        output = IntentEncoderOutput(
            domains=(type(top)(target.owner_agent, top.score),),
            capabilities=tuple(
                type(item)(
                    class_by_label[item.candidate_id].capability_ref,
                    item.score,
                )
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
        return self._policy.decide(
            output, state, registry, entity_bindings=bindings.bindings,
        )
