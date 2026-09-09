"""Domain feasibility proposal, not executable work or authoritative business state."""
from graphlib import CycleError, TopologicalSorter

from jsonschema import Draft202012Validator


TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
OPERATION_PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": "Remaining related writes covering the assigned goal. Not execution permission.",
    "properties": {
        "next_step": TEXT,
        "steps": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {**{key: TEXT for key in (
                "id", "tool", "target", "goal", "preconditions", "effects")},
                "depends_on": {"type": "array", "items": TEXT, "uniqueItems": True}},
            "required": ["id", "tool", "target", "goal", "preconditions", "effects", "depends_on"],
        }},
    },
    "required": ["next_step", "steps"],
}


class OperationPlanError(ValueError):
    """An action proposal cannot be prepared with this operation graph."""


def validate_operation_plan(plan, *, selected_tool, allowed_tools):
    """Check graph structure only; semantic feasibility belongs to domain review."""
    errors = list(Draft202012Validator(OPERATION_PLAN_SCHEMA).iter_errors(plan))
    if errors:
        # jsonschema.message includes the rejected instance. Keep diagnostics
        # structural; tool arguments may contain private business information.
        raise OperationPlanError("operation_plan schema violation: " + errors[0].validator)
    steps = {step["id"]: step for step in plan["steps"]}
    if len(steps) != len(plan["steps"]):
        raise OperationPlanError("operation_plan step IDs must be unique")
    if any(step["tool"] not in allowed_tools for step in steps.values()):
        raise OperationPlanError("operation_plan exceeds assigned action capabilities")
    if any(set(step["depends_on"]) - steps.keys() for step in steps.values()):
        raise OperationPlanError("operation_plan has an unknown dependency")
    cyclic = False
    try:
        tuple(TopologicalSorter({key: step["depends_on"] for key, step in steps.items()}).static_order())
    except CycleError:
        cyclic = True
    if cyclic:
        # The library exception carries model-supplied node IDs; retain the
        # typed structural diagnosis without exporting the rejected graph.
        raise OperationPlanError("operation_plan contains a cycle")
    selected = steps.get(plan["next_step"])
    if selected is None or selected["tool"] != selected_tool:
        raise OperationPlanError("operation_plan next_step must match the proposed tool")
    if selected["depends_on"]:
        raise OperationPlanError("operation_plan next_step has unfinished prerequisites")
