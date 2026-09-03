"""Bind a routing-time media request to one available asset.

The producer of :class:`RoutingMediaNeed` decides whether understanding needs
text extraction or visual reasoning.  This module only selects the referenced
asset and reuses an existing observation when it is sufficient.  Task-level
media planning remains owned by ``MediaRequirementDecision`` after routing.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from application.media_requirement import MediaStage


class RoutingMediaScope(str, Enum):
    CURRENT_TURN = "CURRENT_TURN"
    PRIOR_TURN = "PRIOR_TURN"
    ANY_AVAILABLE = "ANY_AVAILABLE"


class RoutingMediaOutcome(str, Enum):
    NO_MEDIA = "NO_MEDIA"
    REUSE = "REUSE"
    L1 = "L1"
    L2 = "L2"
    AMBIGUOUS = "AMBIGUOUS"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RoutingAssetRef:
    asset_id: str
    checksum: str
    turn_ref: str

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not self.turn_ref.strip():
            raise ValueError("routing asset identity is required")
        if len(self.checksum) != 64:
            raise ValueError("routing asset checksum must be SHA-256")


@dataclass(frozen=True)
class RoutingObservationRef:
    artifact_ref: str
    asset_id: str
    asset_checksum: str
    stage: MediaStage

    def __post_init__(self) -> None:
        if not self.artifact_ref.strip() or not self.asset_id.strip():
            raise ValueError("routing observation identity is required")
        if len(self.asset_checksum) != 64:
            raise ValueError("routing observation checksum must be SHA-256")


@dataclass(frozen=True)
class RoutingMediaNeed:
    """A narrow request produced by deterministic or semantic understanding."""

    stage: MediaStage | None
    scope: RoutingMediaScope = RoutingMediaScope.ANY_AVAILABLE
    asset_id: str | None = None

    @classmethod
    def none(cls) -> "RoutingMediaNeed":
        return cls(stage=None)

    def __post_init__(self) -> None:
        if self.asset_id is not None and not self.asset_id.strip():
            raise ValueError("routing media asset_id cannot be blank")


@dataclass(frozen=True)
class RoutingMediaContext:
    current_assets: tuple[RoutingAssetRef, ...] = ()
    prior_assets: tuple[RoutingAssetRef, ...] = ()
    observations: tuple[RoutingObservationRef, ...] = ()


@dataclass(frozen=True)
class RoutingMediaResult:
    outcome: RoutingMediaOutcome
    asset: RoutingAssetRef | None = None
    stage: MediaStage | None = None
    artifact_ref: str | None = None
    candidates: tuple[RoutingAssetRef, ...] = ()
    reason_code: str = ""


class RoutingMediaProbe:
    """Resolve one declared media need without interpreting natural language."""

    def resolve(
        self,
        need: RoutingMediaNeed,
        context: RoutingMediaContext,
    ) -> RoutingMediaResult:
        if need.stage is None:
            return RoutingMediaResult(
                RoutingMediaOutcome.NO_MEDIA,
                reason_code="MEDIA_NOT_REQUESTED",
            )

        candidates = self._candidates(need, context)
        if not candidates:
            return RoutingMediaResult(
                RoutingMediaOutcome.FAILED,
                stage=need.stage,
                reason_code="MEDIA_NOT_AVAILABLE",
            )
        if len(candidates) > 1:
            return RoutingMediaResult(
                RoutingMediaOutcome.AMBIGUOUS,
                stage=need.stage,
                candidates=candidates,
                reason_code="MEDIA_TARGET_AMBIGUOUS",
            )

        asset = candidates[0]
        reusable = next(
            (
                item for item in context.observations
                if item.asset_id == asset.asset_id
                and item.asset_checksum == asset.checksum
                and item.stage >= need.stage
            ),
            None,
        )
        if reusable is not None:
            return RoutingMediaResult(
                RoutingMediaOutcome.REUSE,
                asset=asset,
                stage=need.stage,
                artifact_ref=reusable.artifact_ref,
                reason_code="MEDIA_OBSERVATION_REUSED",
            )

        outcome = (
            RoutingMediaOutcome.L1
            if need.stage is MediaStage.L1_TEXT_EXTRACTION
            else RoutingMediaOutcome.L2
        )
        return RoutingMediaResult(
            outcome,
            asset=asset,
            stage=need.stage,
            reason_code="MEDIA_PROCESSING_REQUIRED",
        )

    @staticmethod
    def _candidates(
        need: RoutingMediaNeed,
        context: RoutingMediaContext,
    ) -> tuple[RoutingAssetRef, ...]:
        if need.scope is RoutingMediaScope.CURRENT_TURN:
            available = context.current_assets
        elif need.scope is RoutingMediaScope.PRIOR_TURN:
            available = context.prior_assets
        else:
            available = context.current_assets + context.prior_assets
        if need.asset_id is not None:
            return tuple(item for item in available if item.asset_id == need.asset_id)
        return available
