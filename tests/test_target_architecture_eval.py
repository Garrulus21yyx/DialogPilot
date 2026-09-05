from application.capability_safety import SafetyInvariant, SafetyObservation
from evaluation.target_architecture_eval import (
    FunnelLayer,
    TargetArchitectureEvaluator,
    TargetEvalExpectation,
    TargetEvalObservation,
)


def _expectation(case_id="order-direct", capability="order_status", **changes):
    values = {
        "case_id": case_id,
        "capability_id": capability,
        "route_mode": "DIRECT",
        "owner_ids": ("order_logistics",),
        "tool_calls": ("order_lookup",),
        "terminal_state": "COMPLETED",
        "outcome": "COMPLETED",
        "verifier_status": "PASS",
        "max_provider_calls": 0,
        "max_work_items": 1,
        "required_safety_invariants": (SafetyInvariant.UNAUTHORIZED_TOOL,),
    }
    values.update(changes)
    return TargetEvalExpectation(**values)


def _observation(capability="order_status", **changes):
    values = {
        "invocation_admitted": True,
        "route_mode": "DIRECT",
        "owner_ids": ("order_logistics",),
        "registry_fingerprint": "registry:sha256",
        "plan_id": "plan:order-direct",
        "work_item_ids": ("work:order",),
        "consumed_components": ("deterministic", "direct_runtime"),
        "tool_calls": ("order_lookup",),
        "terminal_state": "COMPLETED",
        "committed_receipt_refs": (),
        "outcome": "COMPLETED",
        "verifier_status": "PASS",
        "publication_ref": "publication:order-direct",
        "provider_calls": 0,
        "latency_ms": 12.5,
        "safety_observations": (SafetyObservation(
            capability,
            SafetyInvariant.UNAUTHORIZED_TOOL,
            True,
            "eval:order-tool-allowlist",
        ),),
    }
    values.update(changes)
    return TargetEvalObservation(**values)


def test_six_layer_funnel_passes_only_with_consumption_state_outcome_and_cost():
    report = TargetArchitectureEvaluator().evaluate(((
        _expectation(), _observation(),
    ),))

    assert report.passed is True
    assert tuple(item.layer for item in report.results[0].layers) == tuple(FunnelLayer)
    assert all(item.passed for item in report.results[0].layers)
    assert report.capability_gates == {"order_status": True}


def test_safety_failure_cannot_be_hidden_by_passing_functional_layers():
    observation = _observation(safety_observations=(SafetyObservation(
        "order_status",
        SafetyInvariant.UNAUTHORIZED_TOOL,
        False,
        "eval:forbidden-order-tool-call",
    ),))
    report = TargetArchitectureEvaluator().evaluate(((
        _expectation(), observation,
    ),))

    assert all(item.passed for item in report.results[0].layers)
    assert report.results[0].gate.enabled is False
    assert report.passed is False
    assert report.failed_case_ids == ("order-direct",)


def test_capability_scoped_report_keeps_unrelated_capability_enabled():
    failed_refund = _observation(
        capability="execute_refund",
        route_mode="WORKFLOW",
        owner_ids=("billing_refund",),
        tool_calls=("refund_request_create", "refund_request_create"),
        terminal_state="RECONCILING",
        outcome="RECONCILING",
        verifier_status="UNKNOWN",
        provider_calls=1,
        safety_observations=(SafetyObservation(
            "execute_refund",
            SafetyInvariant.DUPLICATE_SIDE_EFFECT,
            False,
            "eval:duplicate-refund-write",
        ),),
    )
    report = TargetArchitectureEvaluator().evaluate((
        (_expectation(), _observation()),
        (_expectation(
            "refund-write", "execute_refund",
            route_mode="WORKFLOW",
            owner_ids=("billing_refund",),
            tool_calls=("refund_request_create",),
            terminal_state="COMPLETED",
            outcome="COMPLETED",
            max_provider_calls=0,
            required_safety_invariants=(SafetyInvariant.DUPLICATE_SIDE_EFFECT,),
        ), failed_refund),
    ))

    assert report.passed is False
    assert report.capability_gates == {
        "order_status": True,
        "execute_refund": False,
    }
    refund_layers = {
        item.layer: item for item in report.results[1].layers
    }
    assert refund_layers[FunnelLayer.CONSUMPTION].passed is False
    assert refund_layers[FunnelLayer.STATE_SIDE_EFFECT].passed is False
    assert refund_layers[FunnelLayer.OUTCOME].passed is False
    assert refund_layers[FunnelLayer.COST].passed is False


def test_missing_required_safety_observation_fails_the_gate():
    report = TargetArchitectureEvaluator().evaluate(((
        _expectation(), _observation(safety_observations=()),
    ),))

    assert report.results[0].gate.enabled is False
    assert report.results[0].gate.failed_invariants == (
        SafetyInvariant.UNAUTHORIZED_TOOL,
    )


def test_parallel_tool_completion_order_does_not_change_consumption_score():
    report = TargetArchitectureEvaluator().evaluate(((
        _expectation(tool_calls=("refund_status", "catalog_search")),
        _observation(tool_calls=("catalog_search", "refund_status")),
    ),))

    consumption = next(
        item for item in report.results[0].layers
        if item.layer is FunnelLayer.CONSUMPTION
    )
    assert consumption.passed


def test_every_declared_safety_invariant_disables_only_its_capability():
    for invariant in SafetyInvariant:
        report = TargetArchitectureEvaluator().evaluate((
            (
                _expectation(
                    case_id=f"failed-{invariant.value}",
                    capability="capability-a",
                    required_safety_invariants=(invariant,),
                ),
                _observation(
                    capability="capability-a",
                    safety_observations=(SafetyObservation(
                        "capability-a", invariant, False,
                        f"evidence:{invariant.value}",
                    ),),
                ),
            ),
            (
                _expectation(
                    case_id=f"healthy-{invariant.value}",
                    capability="capability-b",
                ),
                _observation(capability="capability-b"),
            ),
        ))

        assert report.capability_gates == {
            "capability-a": False,
            "capability-b": True,
        }
