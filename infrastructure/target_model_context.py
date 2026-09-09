"""Model-facing sections shared by conversation planning and delegated tasks.

Application state remains authoritative. These functions only project it onto
native messages; they neither resolve intent nor update persisted history.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping

from langchain_core.messages import AIMessage, HumanMessage
from application.pending_input_view import pending_input_context, pending_input_fields

CONTRACT_MARKER = "\n\nPlanning capability contract (application configuration):\n"
CONTRACT_FIELDS = frozenset({
    "supported_goals", "goal_descriptions", "domain_capabilities",
    "knowledge_filter_contract", "missing_fields_schema", "registry_fingerprint",
    "conversation_policies",
})


def context_block(name, value):
    """JSON escaping keeps arbitrary source text inside its declared section."""
    return {"type": "text", "text": json.dumps(
        {name: value}, ensure_ascii=False, sort_keys=True,
    )}


def planning_context(payload):
    value = copy.deepcopy(dict(payload))
    supplied = value.get("supplied_interaction_values")
    if supplied:
        aliases = {(item["target_work_item_id"], item["field_name"]): key
                   for key, (item, _) in pending_input_fields(value.get("pending_input") or {}).items()}
        answers = {}
        for item in supplied:
            binding = (item["target_work_item_id"], item["field_name"])
            if binding not in aliases:
                raise ValueError("supplied_input_requires_authoritative_binding")
            answers[aliases[binding]] = item["value"]
        value["supplied_interaction_values"] = answers
    if value.get("pending_input"):
        value["pending_input"] = pending_input_context(value["pending_input"])
    current = value.pop("message")
    user_inputs = {key: value.pop(key) for key in (
        "current_user_decision", "supplied_interaction_values") if key in value}
    if not isinstance(current, str):
        raise ValueError("planning_current_request_requires_text")
    contract = {key: value.pop(key) for key in sorted(CONTRACT_FIELDS) if key in value}
    context = value.pop("conversation_context", {})
    if not isinstance(context, Mapping):
        raise ValueError("planning_conversation_context_requires_object")
    context = dict(context)
    history = context.pop("recent_messages", [])
    messages = []
    provenance = []
    for item in history:
        if not isinstance(item, Mapping) or item.get("role") not in {"user", "assistant"}:
            raise ValueError("planning_history_role_unsupported")
        if not isinstance(item.get("content"), str):
            raise ValueError("planning_history_requires_text")
        cls = HumanMessage if item["role"] == "user" else AIMessage
        messages.append(cls(content=item["content"]))
        provenance.append({key: val for key, val in item.items() if key != "content"})
    # The summary is stable between compactions. Per-turn source watermarks and
    # provenance belong to the dynamic suffix, not ahead of reusable history.
    summary = {key: context.pop(key) for key in ("summary",) if key in context}
    background = HumanMessage(content=[context_block("conversation_summary", summary)])
    value["conversation_context"] = context
    value["history_provenance"] = provenance
    tail = HumanMessage(content=[
        context_block("runtime_context", value),
        context_block("current_request", current),
        *[context_block(key, supplied) for key, supplied in user_inputs.items()],
    ])
    return CONTRACT_MARKER + json.dumps(contract, ensure_ascii=False, sort_keys=True), [background, *messages, tail]


def planning_payload_from_request(request):
    """Read current framework captures and immutable legacy single-JSON captures.

    This is an audit/replay decoder, never a second production input path.
    Captures preserve pre-SDK messages; SDK normalization is tested separately.
    Pending information is a presentation view: replaying a bound interaction
    requires the saved application state, not reconstructed IDs from model input.
    """
    messages = request["messages"]
    system = request.get("system", "")
    if CONTRACT_MARKER not in system:
        if len(messages) != 1 or not isinstance(messages[0]["content"], str):
            raise ValueError("unsupported_planning_capture")
        return json.loads(messages[0]["content"])
    contract = json.loads(system.split(CONTRACT_MARKER, 1)[1])
    background = json.loads(messages[0]["content"][0]["text"])
    runtime, current, *user_inputs = [json.loads(block["text"]) for block in messages[-1]["content"]]
    value = copy.deepcopy(runtime["runtime_context"])
    provenance = value.pop("history_provenance")
    history = messages[1:-1]
    if len(history) != len(provenance):
        raise ValueError("planning_capture_history_mismatch")
    value["conversation_context"].update(background["conversation_summary"])
    value["conversation_context"]["recent_messages"] = []
    for message, source in zip(history, provenance, strict=True):
        expected = "assistant" if source["role"] == "assistant" else "user"
        if message["role"] not in ({"assistant", "ai"} if expected == "assistant" else {"user", "human"}):
            raise ValueError("planning_capture_role_mismatch")
        value["conversation_context"]["recent_messages"].append({**source, "content": message["content"]})
    value.update(contract)
    value["message"] = current["current_request"]
    seen = set()
    for supplied in user_inputs:
        if (len(supplied) != 1 or not set(supplied) <= {
                "current_user_decision", "supplied_interaction_values"} or seen.intersection(supplied)):
            raise ValueError("planning_capture_user_input_invalid")
        seen.update(supplied)
        value.update(supplied)
    return value


def delegated_task_content(payload):
    """Keep the assigned objective distinct from its source conversation and facts."""
    value = dict(payload)
    source = {key: value.pop(key) for key in ("source_conversation", "recent_relevant_turns")}
    task = {key: value.pop(key) for key in ("objective", "arguments", "requirements", "action_proposals_allowed")}
    return [context_block("source_context", source), context_block("runtime_context", value),
            context_block("delegated_task", task)]


def delegated_working_input(history, content, execution_id):
    """Current control facts are pinned; background stays editable SDK history.

    Only application-owned task-message IDs are superseded, never user/tool
    messages. The original working records remain in the run archive/checkpoint.
    """
    sections = {}
    for block in content:
        sections.update(json.loads(block["text"]))
    source = dict(sections["source_context"])
    runtime = dict(sections["runtime_context"])
    task = sections["delegated_task"]
    current = source.pop("source_conversation")
    control = {key: runtime.pop(key) for key in
               ("pending_approval", "action_decisions", "completed_actions") if key in runtime}
    background = HumanMessage(content=[context_block("source_context", source),
        context_block("runtime_context", runtime)], id=f"task-background:{execution_id}")
    pinned = HumanMessage(content=[context_block("delegated_task", task),
        context_block("source_context", {"source_conversation": current}), context_block("runtime_context", control)],
        id=f"task-context:{execution_id}")
    working = [m for m in history if not (isinstance(m, HumanMessage)
        and (m.id or "").startswith(("task-context:", "task-background:")))]
    return [background, *working, pinned], pinned
