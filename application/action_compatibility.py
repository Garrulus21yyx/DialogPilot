"""Necessary resource-state feasibility of declared writes; never execution authority.

Rules belong to the business registry. The checker reasons over possible initial
states, not cached orders: passing cannot establish live eligibility or coverage
of an undeclared user goal. Runtime still obtains approval and calls the owner.
"""
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ActionStateTransition:
    resource: str
    target_argument: str
    states: tuple[str, ...]
    resulting_state: str | None = None  # None preserves this resource's state.

    def __post_init__(self):
        if (not self.resource.strip() or not self.target_argument.strip()
                or not self.states or any(not state.strip() for state in self.states)
                or len(set(self.states)) != len(self.states)
                or self.resulting_state is not None and not self.resulting_state.strip()):
            raise ValueError("action transition requires a resource, target and distinct states")


class ActionCompatibilityError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def order_prepared_actions(pairs, registry):
    """Order actual owner-prepared candidates, never model-declared future work.

    Returns candidate/origin pairs in a feasible order. All candidates were
    prepared against the same current state: their preconditions must intersect.
    Missing effect contracts cannot establish multi-action compatibility.
    Optimistic-version dependent writes require an explicit owner binding; until
    one exists they are unresolved, not silently rebound to a guessed version.
    """
    pairs = tuple(pairs)
    if len(pairs) < 2:
        return pairs
    rules = tuple(registry.action(action.action_ref).state_transition for action, _ in pairs)
    if any(rule is None for rule in rules):
        raise ActionCompatibilityError("ACTION_COMPATIBILITY_UNRESOLVED",
            "The operation owners have not declared all effects; joint execution cannot be established.")
    resources = []
    for (action, _), rule in zip(pairs, rules):
        target = {arg.name: arg.value for arg in action.arguments}.get(rule.target_argument)
        if not isinstance(target, str) or not target:
            raise ActionCompatibilityError("ACTION_TARGET_UNRESOLVED", "A prepared resource has no exact target.")
        resources.append((rule.resource, target))
    keys = tuple(dict.fromkeys(resources))
    initial = tuple(frozenset.intersection(*(frozenset(rule.states)
        for resource, rule in zip(resources, rules) if resource == key)) for key in keys)
    visits = 0

    @lru_cache(None)
    def search(done, states):
        nonlocal visits
        visits += 1
        if visits > 4096:
            raise ActionCompatibilityError("ACTION_COMPATIBILITY_UNRESOLVED", "Compatibility search budget exhausted.")
        if len(done) == len(pairs):
            return ()
        for index, ((action, origin), rule) in enumerate(zip(pairs, rules)):
            if index in done:
                continue
            # An explicit task dependency is not permission to reverse user order.
            if any(other.work_item_id in origin.dependencies and other_index not in done
                   for other_index, (_, other) in enumerate(pairs) if other != origin):
                continue
            position = keys.index(resources[index])
            valid = states[position].intersection(rule.states)
            if not valid:
                continue
            updated = list(states)
            updated[position] = frozenset((rule.resulting_state,)) if rule.resulting_state else valid
            tail = search(done | frozenset((index,)), tuple(updated))
            if tail is not None:
                return (index, *tail)
        return None

    order = search(frozenset(), initial)
    if order is None:
        raise ActionCompatibilityError("ACTION_COMBINATION_INCOMPATIBLE",
            "The prepared operations cannot all execute under their owners' resource-state rules. Choose an alternative.")
    for key in keys:
        members = [action for (action, _), resource in zip(pairs, resources) if resource == key]
        if len(members) > 1 and any(registry.action(action.action_ref).preparation for action in members):
            raise ActionCompatibilityError("ACTION_VERSION_REBINDING_REQUIRED",
                "Related writes require a receipt-bound version contract before joint approval; no version is inferred.")
    return tuple(pairs[index] for index in order)
