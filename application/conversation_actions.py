"""State-scoped model actions, converted once into the existing turn compiler input.

This catalog describes proposals, never executes them. SDK parsing happens in the
provider; the turn compiler and RoutePolicy still own authority and transitions.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import hashlib
import re
from collections import Counter

from jsonschema import Draft202012Validator

from application.conversation_agent import planning_goal_descriptions, planning_output_schema
from application.evidence_query_contract import QUERY_DESCRIPTION
from application.knowledge_tool_contract import knowledge_query_options_schema


_TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
_ORDER = {"order_status", "logistics_status", "cancel_order", "change_address",
          "refund_eligibility", "refund_status", "execute_refund"}
_MEDIA = {"product_identification", "product_assistance", "media_text_read", "media_visual_analysis"}
_KNOWLEDGE = ("general_qa", "refund_policy", "invoice_qa", "product_qa")


@dataclass
class PlanningAction:
    name: str
    description: str
    properties: dict
    required: tuple[str, ...] = ()
    kind: str | None = None
    selections: dict = field(default_factory=dict)
    bound: dict = field(default_factory=dict)
    original_schema: dict | None = None

    @property
    def schema(self):
        if self.original_schema is not None:
            return deepcopy(self.original_schema)
        return {"type": "object", "additionalProperties": False,
                "properties": deepcopy(self.properties), "required": list(self.required)}

    def tool(self):
        return {"name": self.name, "description": self.description, "input_schema": self.schema}

    def convert(self, arguments):
        # SDK owns parsing; standard JSON excludes nonfinite Python floats.
        json.dumps(arguments, allow_nan=False)
        Draft202012Validator(self.schema).validate(arguments)
        if self.kind == "atomic_read":
            if {"approved", "approval_token"}.intersection(arguments):
                raise ValueError("planning_read_uses_reserved_control_fields")
            return {"kind": self.kind, **deepcopy(self.bound), "arguments": deepcopy(arguments)}
        value = deepcopy(arguments)
        for name, choices in self.selections.items():
            if name in value:
                value.update(deepcopy(choices[value.pop(name)]))
        value.update(deepcopy(self.bound))
        if self.kind is not None:
            value["kind"] = self.kind
        if self.kind in _KNOWLEDGE:
            value["resolved_query"] = value.pop("query")
        return value


def _selector(choices, description):
    return {"type": "string", "enum": list(choices), "description": description}


def _entity_choices(payload, field_name):
    candidates = []
    for group in payload.get("entity_bindings", ()):
        if group["field_name"] not in {field_name, "reference"} or group["status"] not in {"UNIQUE", "AMBIGUOUS"}:
            continue
        for item in group["candidates"]:
            bound = {field_name: item["value"], f"{field_name}_source_ref": item["source_ref"]}
            if bound not in candidates:
                candidates.append(bound)
    values = [item[field_name] for item in candidates]
    reserved = {value for value in values if isinstance(value, str)}
    choices = {}
    for index, bound in enumerate(candidates, 1):
        value = bound[field_name]
        if isinstance(value, str) and values.count(value) == 1:
            key = value
        else:
            # Source ambiguity is a real choice; never silently select one source.
            key = f"candidate_{index}"
            while key in reserved or key in choices:
                key = "_" + key
        choices[key] = bound
    return choices


def planning_actions(payload):
    """Expose only supported shortcuts and currently bound state operations.

    Entity selectors retain explicit semantic type/target selection. Approval
    identity and per-field work-item bindings are injected, not model generated.
    """
    descriptions = planning_goal_descriptions()
    observing = (payload.get("conversation_context") or {}).get("observed_execution") is not None
    supported = set(payload.get("supported_goals", ()))
    if supported - descriptions.keys():
        raise ValueError("unsupported_planning_capability")
    active = {f"task_{i}": {"revises_control_id": item["control_id"]}
              for i, item in enumerate(payload.get("active_work_controls", ()), 1)}
    active_description = str({f"task_{i}": item["objective"]
                              for i, item in enumerate(payload.get("active_work_controls", ()), 1)})
    common = {"goal_id": {**_TEXT, "description": "Optional name only when another action depends on this result."},
              "depends_on": {"type": "array", "uniqueItems": True, "items": _TEXT,
                             "description": "Other goal_id names whose results are prerequisites; omit for independent work."}}
    if active:
        common["revises"] = _selector(active, "Only for correcting/replacing an existing objective: " + active_description)
    actions = []

    def goal(name, kind, properties=None, required=(), description=None, selections=None):
        selection = {"revises": active} if active else {}
        selection.update(selections or {})
        action = PlanningAction(name, description or descriptions[kind],
                                {**deepcopy(common), **(properties or {})}, required, kind, selection)
        actions.append(action)
        return action

    for knowledge in _KNOWLEDGE:
        if knowledge not in supported:
            continue
        goal("knowledge_search" if knowledge == "general_qa" else knowledge, knowledge,
             {"query": {**_TEXT, "maxLength": 4000, "description": QUERY_DESCRIPTION},
              "knowledge_options": knowledge_query_options_schema(payload.get("knowledge_filter_contract"))},
             ("query",), descriptions[knowledge] + " "
             "Include known subject, conditions and negation in query. Missing personal eligibility details do not "
             "prevent looking up general rules. Optional hard filters require explicit supported scope; omit unknown values.")
    for kind in sorted(supported - set(_KNOWLEDGE) - {"atomic_read", "delegate_task", "continue_active_work", "cancel_active_work"}):
        properties, required, selections = {}, [], {}
        field_name = "order_id" if kind in _ORDER else "asset_id" if kind in _MEDIA else None
        if field_name:
            choices = _entity_choices(payload, field_name)
            if not choices:
                continue  # Open domain investigation remains available below.
            labels = choices
            properties["entity"] = _selector(choices, f"Select the {field_name} from scoped candidates: {labels}")
            required.append("entity")
            selections["entity"] = choices
        if kind == "change_address":
            properties["new_address"] = {**_TEXT, "description": "The user's verbatim new shipping address."}
            required.append("new_address")
        goal(kind, kind, properties, tuple(required), selections=selections)
    domains = payload.get("domain_capabilities", ())
    if "delegate_task" in supported and domains:
        properties = {
            "target_agent": _selector({item["agent_id"]: item for item in domains},
                                      "Select the domain for an open investigation or business-change preparation, not a second planner for an already explicit tool query."),
            "objective": {**_TEXT, "description": "Complete desired outcome including user constraints, not a prescribed tool sequence. Keep changes sharing object state or a one-time capability together so their compatibility can be assessed before preparation."},
            "allow_action_proposals": {"type": "boolean", "description": "True when the user's delegated outcome requests a business change, including a change conditional on eligibility. False for information-only requests. This permits preparing a proposal, never grants approval or bypasses its submission policy."},
            "new_address": {**_TEXT, "description": "Optional user's verbatim new shipping address when relevant to this objective."},
        }
        selections = {}
        for name, field_name in (("order", "order_id"), ("media", "asset_id")):
            choices = _entity_choices(payload, field_name)
            if choices:
                properties[name] = _selector(choices, f"Optional relevant {field_name}: " +
                                            str(choices))
                selections[name] = choices
        goal("delegate_task", "delegate_task", properties,
             ("target_agent", "objective", "allow_action_proposals"), selections=selections)
    for name, candidates in (
        ("continue_active_work", payload.get("resumable_work", ())),
        ("cancel_active_work", payload.get("active_work_controls", ())),
    ):
        if name not in supported or not candidates or observing and name == "continue_active_work":
            continue
        choices = {f"task_{i}": {"revises_control_id": item["control_id"]} for i, item in enumerate(candidates, 1)}
        meaning = ("Resume the unchanged pending objective using its saved tools and arguments. "
                   "A pending approval also requires review_action; resuming is not approval."
                   if name == "continue_active_work" else
                   "Cancel only the selected active conversation objective; this does not cancel a business order.")
        desc = meaning + " Targets: " + str({f"task_{i}": item["objective"] for i, item in enumerate(candidates, 1)})
        action = goal(name, name, description=desc)
        action.properties.pop("revises", None)
        action.selections.pop("revises", None)
        if len(choices) == 1:
            action.bound = next(iter(choices.values()))
        else:
            action.properties["target"] = _selector(choices, desc)
            action.required = ("target",)
            action.selections["target"] = choices
    if payload.get("pending_approval") and not observing:
        actions.append(PlanningAction("review_action",
            "Approve or decline the current prepared action. Approve only explicit assent to its exact unchanged "
            "arguments now. Questions/conditional assent are not approval. Corrections use a revised goal instead. "
            "Decline with a replacement/continuing objective also needs that revised/continuation action; "
            "decline alone stops the old objective. Pure approval needs no duplicate task. Preserve independent questions.",
            {"decision": {"type": "string", "enum": ["approve", "decline"]}}, ("decision",),
            bound={"approval_id": payload["pending_approval"]["approval_id"]}))
    pending = payload.get("pending_input") if not observing else None
    if pending and pending.get("requested_fields"):
        fields = {f"field_{i}": item for i, item in enumerate(pending["requested_fields"], 1)}
        actions.append(PlanningAction("supply_input",
            "Supply only the requested values actually answered by the user, including a partial subset. "
            "Do not recreate the task. Independent new requests can accompany this action.",
            {"values": {"type": "object", "additionalProperties": False, "minProperties": 1,
                        "properties": {key: {"type": ["string", "number", "boolean"],
                            "description": str(item)} for key, item in fields.items()}}}, ("values",),
            bound={"fields": fields}))
    actions.append(PlanningAction("unsupported_request",
        "The requested objective is outside the available capabilities; missing identifiers alone are not out of scope.", {}))
    catalog = payload.get("atomic_reads", ())
    counts = Counter(item["tool_id"] for item in catalog)
    names = {action.name for action in actions} | {"bind_read_goals"}
    for item in catalog:
        name = item["tool_id"]
        if counts[name] != 1 or name in names or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            name = "read_" + hashlib.sha256(json.dumps(
                [item["owner_agent"], item["tool_id"]]).encode()).hexdigest()[:24]
        if name in names:
            raise ValueError("planning_action_name_collision")
        names.add(name)
        actions.append(PlanningAction(name,
            f"Read tool {item['tool_id']} for domain {item['owner_agent']}. " + item["description"],
            {}, kind="atomic_read", bound={"target_agent": item["owner_agent"], "tool_id": item["tool_id"]},
            original_schema=item["input_schema"]))
    if catalog:
        actions.append(PlanningAction("bind_read_goals",
            "Optional scheduling metadata, only when another goal depends on an atomic read. "
            "atomic_call counts only atomic read calls in this response, starting at 1. "
            "This does not execute work. Do not use for standalone reads.",
            {"bindings": {"type": "array", "minItems": 1, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["atomic_call", "goal_id"], "properties": {
                    "atomic_call": {"type": "integer", "minimum": 1},
                    "goal_id": _TEXT, "depends_on": common["depends_on"],
                }}}}, ("bindings",)))
    return tuple(actions)


def action_proposal(actions, calls, text):
    """Validate a whole SDK call batch before producing any executable proposal."""
    if not calls:
        if not text.strip():
            raise ValueError("planning_requires_action_or_text")
        return {"status": "respond", "response": text.strip()}
    if len(calls) > 7 or len({call["id"] for call in calls}) != len(calls):
        raise ValueError("planning_action_batch_invalid")
    by_name = {action.name: action for action in actions}
    proposal = {"status": "resolved"}
    atomic_goals, read_bindings = [], None
    for call in calls:
        if call["name"] not in by_name:
            raise ValueError("planning_action_unavailable")
        action = by_name[call["name"]]
        value = action.convert(call["args"])
        if action.kind:
            if action.kind == "atomic_read":
                value["goal_id"] = "atomic:" + call["id"]
                atomic_goals.append(value)
            proposal.setdefault("goals", []).append(value)
        elif action.name == "bind_read_goals":
            if read_bindings is not None:
                raise ValueError("planning_duplicate_read_bindings")
            read_bindings = value["bindings"]
        elif action.name == "unsupported_request":
            if len(calls) != 1:
                raise ValueError("planning_unsupported_cannot_mix_actions")
            return {"status": "out_of_scope"}
        elif action.name == "review_action":
            if "approval_decision" in proposal:
                raise ValueError("planning_duplicate_approval")
            proposal["approval_decision"] = value
        elif action.name == "supply_input":
            inputs = proposal.setdefault("input_values", [])
            for key, answer in value["values"].items():
                bound = value["fields"][key]
                if any((item["target_work_item_id"], item["field_name"]) ==
                       (bound["target_work_item_id"], bound["field_name"]) for item in inputs):
                    raise ValueError("planning_duplicate_input")
                inputs.append({"target_work_item_id": bound["target_work_item_id"],
                               "field_name": bound["field_name"], "value": answer})
    seen = set()
    for binding in read_bindings or ():
        index = binding["atomic_call"]
        if index in seen or index > len(atomic_goals):
            raise ValueError("planning_invalid_read_binding")
        seen.add(index)
        atomic_goals[index - 1].update({key: val for key, val in binding.items() if key != "atomic_call"})
    if len(calls) - int(read_bindings is not None) > 6:
        raise ValueError("planning_action_batch_invalid")
    Draft202012Validator(planning_output_schema()).validate(proposal)
    return proposal
