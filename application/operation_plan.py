"""Domain feasibility proposal, not executable work or authoritative business state."""
from graphlib import CycleError, TopologicalSorter
from copy import deepcopy

from jsonschema import Draft202012Validator


TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
OPERATION_PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": "Remaining related writes, including known sibling operations on the same resource as references only. Not execution permission. Declare each future operation once per batch; node IDs are local to this proposal.",
    "properties": {
        "current": {"type": "object", "additionalProperties": False,
            "description": "This tool call is the ready current action; its tool and target come from the call, not this plan. Future dependencies may reference current.",
            "properties": {key: TEXT for key in ("goal", "preconditions", "effects")},
            "required": ["goal", "preconditions", "effects"]},
        "remaining_steps": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {**{key: TEXT for key in (
                "id", "tool", "target", "goal", "preconditions", "effects")},
                "target": {**TEXT, "description": "Exact resource ID (for example the order_id value), not a prose description. References another operation only for compatibility, not permission to execute it."},
                "depends_on": {"type": "array", "items": TEXT, "uniqueItems": True}},
            "required": ["id", "tool", "target", "goal", "preconditions", "effects", "depends_on"],
        }},
    },
    "required": ["current", "remaining_steps"],
}


class OperationPlanError(ValueError):
    """An action proposal cannot be prepared with this operation graph."""


def operation_plan_schema(allowed_tools):
    """The plan uses the same callable names as this task's preparation tools."""
    schema = deepcopy(OPERATION_PLAN_SCHEMA)
    schema["properties"]["remaining_steps"]["items"]["properties"]["tool"] = {
        **TEXT, "enum": sorted(set(allowed_tools)),
        "description": "Exact registered preparation name from this enum, not the underlying business operation name. A future reference need not be callable by this worker and grants no permission.",
    }
    return schema


def validate_operation_plan(plan, *, selected_tool, allowed_tools):
    """Check graph structure; registered transitions and semantic coverage follow."""
    allowed_tools = frozenset(allowed_tools)
    errors = list(Draft202012Validator(operation_plan_schema(allowed_tools)).iter_errors(plan))
    if errors:
        if errors[0].validator == "enum":
            raise OperationPlanError(
                "operation_plan tool name is not a callable preparation capability. "
                "Use one of: " + ", ".join(sorted(allowed_tools)) +
                ". Correct the tool name while preserving all remaining assigned goals and dependencies. "
                "This naming error does not establish that a business operation is unavailable.")
        # jsonschema.message includes the rejected instance. Keep diagnostics
        # structural; tool arguments may contain private business information.
        raise OperationPlanError("operation_plan schema violation: " + errors[0].validator)
    if selected_tool not in allowed_tools:
        raise OperationPlanError("current action exceeds assigned capabilities")
    steps = {step["id"]: step for step in plan["remaining_steps"]}
    if "current" in steps or len(steps) != len(plan["remaining_steps"]):
        raise OperationPlanError("operation_plan step IDs must be unique")
    if any(set(step["depends_on"]) - (steps.keys() | {"current"}) for step in steps.values()):
        raise OperationPlanError("operation_plan has an unknown dependency")
    cyclic = False
    try:
        tuple(TopologicalSorter({"current": (), **{key: step["depends_on"] for key, step in steps.items()}}).static_order())
    except CycleError:
        cyclic = True
    if cyclic:
        # The library exception carries model-supplied node IDs; retain the
        # typed structural diagnosis without exporting the rejected graph.
        raise OperationPlanError("operation_plan contains a cycle")
