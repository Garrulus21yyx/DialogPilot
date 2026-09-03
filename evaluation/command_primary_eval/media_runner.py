"""Thin runner for explicit routing-probe and perception evaluation."""
from __future__ import annotations

from evaluation.command_primary_eval.direct_runner import DirectRunner
from evaluation.command_primary_eval.media import MediaDirectAdapter


class MediaDirectRunner(DirectRunner):
    def __init__(self, adapter: MediaDirectAdapter) -> None:
        super().__init__(
            adapter,
            component="media_routing_probe_perception",
            evaluator_version="media-direct-runner-v1",
            default_configuration={
                "evaluated_scope": "routing_probe_and_perception_artifact_only",
            },
        )
