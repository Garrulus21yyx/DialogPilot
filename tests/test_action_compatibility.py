"""Actual candidate ordering checked against an independent state simulator."""
from itertools import permutations, product
from types import SimpleNamespace

import pytest

from application.action_compatibility import ActionCompatibilityError, ActionStateTransition, order_prepared_actions
from application.work_item import ArgumentValue


def candidates(rules, targets=None, dependencies=None, preparations=None):
    definitions = {str(index): SimpleNamespace(state_transition=rule,
        preparation=(preparations or {}).get(index)) for index, rule in enumerate(rules)}
    registry = SimpleNamespace(action=lambda ref: definitions[ref])
    pairs = tuple((SimpleNamespace(action_ref=str(index), arguments=(ArgumentValue.create(
        "target", (targets or {}).get(index, "same")),)), SimpleNamespace(
        work_item_id=str(index), dependencies=(dependencies or {}).get(index, ()))) for index in range(len(rules)))
    return pairs, registry


def test_generated_ordering_matches_state_machine():
    definitions = [ActionStateTransition("order", "target", states, result)
        for states in (("A",), ("B",), ("A", "B")) for result in (None, "A", "B")]
    for rules in product(definitions, repeat=3):
        pairs, registry = candidates(rules)
        possible = []
        for order in permutations(range(3)):
            initial = set.intersection(*(set(rule.states) for rule in rules))
            for state in initial:
                for index in order:
                    if state not in rules[index].states:
                        break
                    state = rules[index].resulting_state or state
                else:
                    possible.append(order)
        if possible:
            ordered = order_prepared_actions(pairs, registry)
            assert tuple(int(action.action_ref) for action, _ in ordered) in possible
        else:
            with pytest.raises(ActionCompatibilityError) as error:
                order_prepared_actions(pairs, registry)
            assert error.value.code == "ACTION_COMBINATION_INCOMPATIBLE"


def test_ordering_and_target_independence():
    cancel = ActionStateTransition("order", "target", ("paid",), "cancelled")
    change = ActionStateTransition("order", "target", ("paid",))
    pairs, registry = candidates((cancel, change))
    assert order_prepared_actions(pairs, registry) == pairs[::-1]
    pairs, registry = candidates((cancel, change), dependencies={1: ("0",)})
    with pytest.raises(ActionCompatibilityError):
        order_prepared_actions(pairs, registry)
    pairs, registry = candidates((cancel, cancel), targets={1: "different"})
    assert len(order_prepared_actions(pairs, registry)) == 2


def test_unknown_effect_and_version_are_unresolved_not_business_conflict():
    change = ActionStateTransition("order", "target", ("paid",))
    for rules, preparations, code in (
        ((change, None), {}, "ACTION_COMPATIBILITY_UNRESOLVED"),
        ((change, change), {0: object()}, "ACTION_VERSION_REBINDING_REQUIRED"),
    ):
        pairs, registry = candidates(rules, preparations=preparations)
        with pytest.raises(ActionCompatibilityError) as error:
            order_prepared_actions(pairs, registry)
        assert error.value.code == code
