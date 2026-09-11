"""Necessary resource-state feasibility of declared writes; never execution authority.

Rules belong to the business registry. The checker reasons over possible initial
states, not cached orders: passing cannot establish live eligibility or coverage
of an undeclared user goal. Runtime still obtains approval and calls the owner.
"""
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations


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


def validate_action_compatibility(candidate, rules):
    """Check a ready unordered batch plus declared future dependencies.

    `rules` maps registered preparation names to owner-defined transitions. An
    unmodelled operation stays with semantic review; it is not declared safe by
    this function. Current permissions and graph structure are checked separately.
    """
    current = tuple(candidate.get("actions", (candidate,)))
    nodes = {}
    roots = []
    for batch_index, proposal in enumerate(current):
        prefix = f"batch{batch_index}:"
        root = prefix + "current"
        roots.append(root)
        rule = rules.get(proposal["tool"])
        target = proposal["arguments"].get(rule.target_argument) if rule else None
        nodes[root] = (proposal["tool"], rule, target, frozenset())
        plan = proposal["arguments"].get("operation_plan", {})
        for step in plan.get("remaining_steps", ()):
            nodes[prefix + step["id"]] = (step["tool"], rules.get(step["tool"]), step["target"],
                frozenset(prefix + dependency for dependency in step["depends_on"]))

    for _, rule, target, _ in nodes.values():
        if rule and (not isinstance(target, str) or not target.strip()):
            raise ActionCompatibilityError("ACTION_TARGET_UNRESOLVED",
                "Resolve the exact resource ID before preparation; remaining target is the ID, not a description.")

    # Unordered batch members must remain executable in either relative order.
    # Otherwise request a sequence, not an unnecessary choice between both goals.
    for left, right in combinations(roots, 2):
        a, b = nodes[left], nodes[right]
        if not a[1] or not b[1] or (a[1].resource, a[2]) != (b[1].resource, b[2]):
            continue
        def possible(first, second):
            return any((first.resulting_state or state) in second.states for state in first.states)
        ab, ba = possible(a[1], b[1]), possible(b[1], a[1])
        if not ab and not ba and len(nodes) == 2:
            raise ActionCompatibilityError("ACTION_COMBINATION_INCOMPATIBLE",
                f"{a[0]} and {b[0]} cannot both execute on the same resource under registered state rules. "
                "Prepare neither; explain the conflict and use request_user_input to let the user choose. Preserve unrelated goals.")
        common = set(a[1].states) & set(b[1].states)
        if not ab or not ba or not common:
            raise ActionCompatibilityError("ACTION_SEQUENCE_REQUIRED",
                "This ready batch is not order-independent. Keep both goals and prepare the valid first action "
                "with the rest in operation_plan; respect the user's requested order.")

    # Unknown effects are not "preserve". They could restore or change a resource;
    # leave the longer sequence to the existing semantic review instead of proving
    # an impossibility by silently treating an unmodelled action as a no-op.
    if any(rule is None for _, rule, _, _ in nodes.values()):
        return "UNMODELLED_EFFECTS"

    # Search a joint state: independently feasible resource orders can still form
    # an impossible global ordering through dependencies on other resources.
    resources = sorted({(rule.resource, target) for _, rule, target, _ in nodes.values() if rule})
    positions = {resource: index for index, resource in enumerate(resources)}
    initial = tuple(frozenset(state for _, rule, target, _ in nodes.values()
                             if rule and (rule.resource, target) == resource for state in rule.states)
                    for resource in resources)
    initial = list(initial)
    for key in roots:
        _, rule, target, _ = nodes[key]
        index = positions[(rule.resource, target)]
        initial[index] = initial[index].intersection(rule.states)
    initial = tuple(initial)
    visits = 0

    @lru_cache(None)
    def feasible(done, states, unordered=True):
        nonlocal visits
        visits += 1
        if visits > 4096:
            raise ActionCompatibilityError("ACTION_COMPATIBILITY_UNRESOLVED",
                "The declared operation graph exceeds the bounded feasibility check. "
                "Do not prepare a partial set as if the whole graph were checked.")
        if len(done) == len(nodes):
            return True
        outcomes = []
        batch_pending = not set(roots).issubset(done)
        for key, (_, rule, target, dependencies) in nodes.items():
            if (key in done or not dependencies.issubset(done)
                    or key not in roots and not set(roots).issubset(done)):
                continue
            updated = list(states)
            if rule:
                index = positions[(rule.resource, target)]
                valid = states[index].intersection(rule.states)
                if not valid:
                    if batch_pending and unordered:
                        return False
                    continue
                updated[index] = frozenset((rule.resulting_state,)) if rule.resulting_state else valid
            outcome = feasible(done | frozenset((key,)), tuple(updated), unordered)
            if batch_pending and unordered:
                if not outcome:
                    return False
                outcomes.append(outcome)
            elif outcome:
                return True
        return bool(outcomes)

    if not feasible(frozenset(), initial):
        if len(roots) > 1 and feasible(frozenset(), initial, False):
            raise ActionCompatibilityError("ACTION_SEQUENCE_REQUIRED",
                "Only some orderings of this batch preserve the remaining operations. "
                "Keep the goals, prepare a single first action and declare the required sequence; "
                "do not offer the set as order-independent or silently change a user-required ordering.")
        raise ActionCompatibilityError("ACTION_PLAN_INFEASIBLE",
            "The proposed first action and dependencies leave a later declared operation impossible "
            "under registered resource-state rules. Prepare nothing. Check a different ordering; "
            "if none is legal, explain the tradeoff and use request_user_input. Do not silently drop a goal.")
    return "STATE_COMPATIBLE"
