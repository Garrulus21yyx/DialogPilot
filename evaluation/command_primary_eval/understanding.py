"""Thin direct runner for the Turn Understanding boundary."""
from __future__ import annotations

from typing import Protocol

from evaluation.command_primary_eval.contracts import (
    DirectEvaluationResult,
    EvalCase,
)
from evaluation.command_primary_eval.direct_runner import DirectRunner


UnderstandingDirectResult = DirectEvaluationResult


class UnderstandingDirectAdapter(Protocol):
    version: str

    async def evaluate(self, case: EvalCase) -> UnderstandingDirectResult: ...


class UnderstandingDirectRunner(DirectRunner):
    """Persist one state-aware Understanding direct run."""

    evaluator_version = "understanding-direct-runner-v1"

    def __init__(self, adapter: UnderstandingDirectAdapter) -> None:
        super().__init__(
            adapter,
            component="understanding",
            evaluator_version=self.evaluator_version,
        )
