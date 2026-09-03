"""Direct evaluation of an explicit routing-media need and perception artifact."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

from application.media_requirement import MediaRequirementDecision, MediaStage
from application.perception import PerceptionBatch, TieredPerceptionService
from application.routing_media_probe import (
    RoutingMediaContext,
    RoutingMediaNeed,
    RoutingMediaOutcome,
    RoutingMediaProbe,
    RoutingMediaResult,
)
from evaluation.command_primary_eval.contracts import (
    CheckResult,
    CostResult,
    DirectEvaluationResult,
    EvalCase,
)


@dataclass(frozen=True)
class MediaDirectRequest:
    """Typed input prepared by the dataset adapter, never inferred from text."""

    need: RoutingMediaNeed
    context: RoutingMediaContext
    perception_decision: MediaRequirementDecision | None
    tenant_id: str
    user_id: str


@dataclass(frozen=True)
class MediaArtifactObservation:
    asset_id: str | None
    stage: MediaStage | None
    artifact_refs: tuple[str, ...]
    perception_statuses: tuple[str, ...]


@dataclass(frozen=True)
class MediaConsumption:
    artifact_refs: tuple[str, ...]
    outcome: str


MediaRequestLoader = Callable[[EvalCase], MediaDirectRequest]
MediaConsumer = Callable[
    [EvalCase, MediaArtifactObservation],
    Awaitable[MediaConsumption],
]


class MediaDirectAdapter:
    """Measure routing probe, perception artifact, and injected consumption."""

    version = "media-routing-perception-adapter-v1"

    def __init__(
        self,
        *,
        probe: RoutingMediaProbe,
        perception: TieredPerceptionService,
        request_loader: MediaRequestLoader,
        consumer: MediaConsumer,
    ) -> None:
        self._probe = probe
        self._perception = perception
        self._request_loader = request_loader
        self._consumer = consumer

    async def evaluate(self, case: EvalCase) -> DirectEvaluationResult:
        started = perf_counter()
        request = self._request_loader(case)
        resolution = self._probe.resolve(request.need, request.context)
        observation, provider_calls = await self._observe(request, resolution)
        consumed = await self._consumer(case, observation)

        actual_trigger = (
            "SKIPPED" if request.need.stage is None else "INVOKED"
        )
        expected_trigger = str(case.expected["trigger"])
        expected_probe = str(case.expected["probe_outcome"])
        actual_artifact = {
            "asset_id": observation.asset_id,
            "stage": observation.stage.name if observation.stage else None,
            "artifact_refs": list(observation.artifact_refs),
        }
        expected_artifact = dict(case.expected["artifact"])
        expected_consumption = tuple(case.expected["consumed_artifact_refs"])
        expected_outcome = str(case.expected["outcome"])

        return DirectEvaluationResult(
            trigger=CheckResult(
                actual_trigger == expected_trigger
                and resolution.outcome.value == expected_probe,
                {
                    "expected": expected_trigger,
                    "actual": actual_trigger,
                    "expected_probe_outcome": expected_probe,
                    "actual_probe_outcome": resolution.outcome.value,
                },
            ),
            artifact=CheckResult(
                actual_artifact == expected_artifact,
                {"expected": expected_artifact, "actual": actual_artifact},
            ),
            consumption=CheckResult(
                consumed.artifact_refs == expected_consumption,
                {
                    "expected_artifact_refs": expected_consumption,
                    "actual_artifact_refs": consumed.artifact_refs,
                },
            ),
            outcome=CheckResult(
                consumed.outcome == expected_outcome,
                {
                    "expected": expected_outcome,
                    "actual": consumed.outcome,
                    "perception_statuses": observation.perception_statuses,
                },
            ),
            cost=CostResult(
                latency_ms=(perf_counter() - started) * 1000,
                token_usage=0,
                invocation_count=provider_calls,
            ),
        )

    async def _observe(
        self,
        request: MediaDirectRequest,
        resolution: RoutingMediaResult,
    ) -> tuple[MediaArtifactObservation, int]:
        if resolution.outcome is RoutingMediaOutcome.REUSE:
            return MediaArtifactObservation(
                asset_id=resolution.asset.asset_id if resolution.asset else None,
                stage=resolution.stage,
                artifact_refs=(resolution.artifact_ref,) if resolution.artifact_ref else (),
                perception_statuses=("REUSED",),
            ), 0
        if resolution.outcome not in {
            RoutingMediaOutcome.L1,
            RoutingMediaOutcome.L2,
        }:
            return MediaArtifactObservation(
                asset_id=resolution.asset.asset_id if resolution.asset else None,
                stage=resolution.stage,
                artifact_refs=(),
                perception_statuses=(),
            ), 0
        if request.perception_decision is None:
            raise ValueError("perception decision is required for L1/L2 evaluation")

        batch: PerceptionBatch = await asyncio.to_thread(
            self._perception.execute_with_artifacts,
            request.perception_decision,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
        )
        selected_asset = resolution.asset.asset_id if resolution.asset else None
        outcomes = tuple(
            item for item in batch.outcomes if item.asset_id == selected_asset
        )
        refs = tuple(ref for item in outcomes for ref in item.artifact_refs)
        return MediaArtifactObservation(
            asset_id=selected_asset,
            stage=resolution.stage,
            artifact_refs=refs,
            perception_statuses=tuple(item.status.value for item in outcomes),
        ), 1
