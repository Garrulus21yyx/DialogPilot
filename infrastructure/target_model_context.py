"""Model-facing sections shared by conversation planning and delegated tasks.

Application state remains authoritative. These functions only project it onto
native messages; they neither resolve intent nor update persisted history.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping

from langchain_core.messages import AIMessage, HumanMessage

CONTRACT_MARKER = "\n\nPlanning capability contract (application configuration):\n"
CONTRACT_FIELDS = frozenset({
    "supported_goals", "goal_descriptions", "domain_capabilities",
    "knowledge_filter_contract", "missing_fields_schema", "registry_fingerprint",
})


def context_block(name, value):
    """JSON escaping keeps arbitrary source text inside its declared section."""
    return {"type": "text", "text": json.dumps(
        {name: value}, ensure_ascii=False, sort_keys=True,
    )}


def planning_context(payload):
    value = copy.deepcopy(dict(payload))
    current = value.pop("message")
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
    ])
    return CONTRACT_MARKER + json.dumps(contract, ensure_ascii=False, sort_keys=True), [background, *messages, tail]


def planning_payload_from_request(request):
    """Read current framework captures and immutable legacy single-JSON captures.

    This is an audit/replay decoder, never a second production input path.
    Captures preserve pre-SDK messages; SDK normalization is tested separately.
    """
    messages = request["messages"]
    system = request.get("system", "")
    if CONTRACT_MARKER not in system:
        if len(messages) != 1 or not isinstance(messages[0]["content"], str):
            raise ValueError("unsupported_planning_capture")
        return json.loads(messages[0]["content"])
    contract = json.loads(system.split(CONTRACT_MARKER, 1)[1])
    background = json.loads(messages[0]["content"][0]["text"])
    runtime, current = [json.loads(block["text"]) for block in messages[-1]["content"]]
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
    return value


def delegated_task_content(payload):
    """Keep the assigned objective distinct from its source conversation and facts."""
    value = dict(payload)
    source = {key: value.pop(key) for key in ("source_conversation", "recent_relevant_turns")}
    task = {key: value.pop(key) for key in ("objective", "arguments", "requirements", "action_proposals_allowed")}
    return [context_block("source_context", source), context_block("runtime_context", value),
            context_block("delegated_task", task)]
