"""Positive routing-media binding paths."""
from application.media_requirement import MediaStage
from application.routing_media_probe import (
    RoutingAssetRef,
    RoutingMediaContext,
    RoutingMediaNeed,
    RoutingMediaOutcome,
    RoutingMediaProbe,
    RoutingMediaScope,
    RoutingObservationRef,
)


CURRENT = RoutingAssetRef("asset-current", "a" * 64, "turn-current")
PRIOR = RoutingAssetRef("asset-prior", "b" * 64, "turn-prior")


def test_no_media_need_does_not_select_an_asset():
    result = RoutingMediaProbe().resolve(
        RoutingMediaNeed.none(),
        RoutingMediaContext(current_assets=(CURRENT,)),
    )

    assert result.outcome is RoutingMediaOutcome.NO_MEDIA
    assert result.asset is None


def test_current_turn_l1_need_selects_the_only_current_asset():
    result = RoutingMediaProbe().resolve(
        RoutingMediaNeed(
            MediaStage.L1_TEXT_EXTRACTION,
            RoutingMediaScope.CURRENT_TURN,
        ),
        RoutingMediaContext(current_assets=(CURRENT,), prior_assets=(PRIOR,)),
    )

    assert result.outcome is RoutingMediaOutcome.L1
    assert result.asset == CURRENT


def test_exact_prior_asset_can_be_selected():
    result = RoutingMediaProbe().resolve(
        RoutingMediaNeed(
            MediaStage.L1_TEXT_EXTRACTION,
            RoutingMediaScope.PRIOR_TURN,
            PRIOR.asset_id,
        ),
        RoutingMediaContext(current_assets=(CURRENT,), prior_assets=(PRIOR,)),
    )

    assert result.outcome is RoutingMediaOutcome.L1
    assert result.asset == PRIOR


def test_existing_sufficient_observation_is_reused():
    observation = RoutingObservationRef(
        "parse-result-1",
        CURRENT.asset_id,
        CURRENT.checksum,
        MediaStage.L1_TEXT_EXTRACTION,
    )

    result = RoutingMediaProbe().resolve(
        RoutingMediaNeed(
            MediaStage.L1_TEXT_EXTRACTION,
            RoutingMediaScope.CURRENT_TURN,
        ),
        RoutingMediaContext(
            current_assets=(CURRENT,),
            observations=(observation,),
        ),
    )

    assert result.outcome is RoutingMediaOutcome.REUSE
    assert result.artifact_ref == "parse-result-1"


def test_l2_need_is_preserved_for_the_perception_provider():
    result = RoutingMediaProbe().resolve(
        RoutingMediaNeed(
            MediaStage.L2_VISUAL_REASONING,
            RoutingMediaScope.CURRENT_TURN,
        ),
        RoutingMediaContext(current_assets=(CURRENT,)),
    )

    assert result.outcome is RoutingMediaOutcome.L2
    assert result.stage is MediaStage.L2_VISUAL_REASONING


def test_multiple_assets_require_upstream_clarification():
    second = RoutingAssetRef("asset-second", "c" * 64, "turn-current")

    result = RoutingMediaProbe().resolve(
        RoutingMediaNeed(
            MediaStage.L1_TEXT_EXTRACTION,
            RoutingMediaScope.CURRENT_TURN,
        ),
        RoutingMediaContext(current_assets=(CURRENT, second)),
    )

    assert result.outcome is RoutingMediaOutcome.AMBIGUOUS
    assert result.candidates == (CURRENT, second)


def test_missing_requested_asset_is_a_typed_failure():
    result = RoutingMediaProbe().resolve(
        RoutingMediaNeed(
            MediaStage.L1_TEXT_EXTRACTION,
            RoutingMediaScope.PRIOR_TURN,
            "missing-asset",
        ),
        RoutingMediaContext(current_assets=(CURRENT,)),
    )

    assert result.outcome is RoutingMediaOutcome.FAILED
    assert result.reason_code == "MEDIA_NOT_AVAILABLE"
