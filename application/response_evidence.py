"""Display projection; business values are preserved, provenance stays in audit.

Only known envelope metadata is removed. Never recursively redact business keys
or customer text: an order ID is useful, an internal receipt ID is not a citation.
"""
from copy import deepcopy
import json


def verification_evidence(snapshot):
    """Reviewer view of the same snapshot; never derive authorization or status.

    Preserve business bodies and historical evidence, but remove planner-facing
    instructions/catalog metadata. Exact duplicates are not independent evidence.
    """
    result = deepcopy(snapshot)
    policy = result.pop("capability_policy", None)
    if isinstance(policy, dict):
        result["policy_excerpts"] = [
            {"action": row.get("tool_id"), "text": row["description"]}
            for row in policy.get("business_actions", ()) if row.get("description")]
        result["conversation_constraints"] = list(dict.fromkeys(
            row["conversation_policy"] for row in policy.get("agents", ())
            if row.get("conversation_policy")))
    facts = result.get("facts", [])
    unique, indexes, seen = [], {}, {}
    for index, fact in enumerate(facts):
        identity = json.dumps(fact, sort_keys=True, ensure_ascii=False)
        if identity not in seen:
            seen[identity] = len(unique)
            unique.append(fact)
        indexes[index] = seen[identity]
    if "facts" in result:
        result["facts"] = unique
    for outcome in result.get("outcomes", ()):
        outcome.pop("control", None)
        outcome.pop("owner_agent", None)
        if "requested_evidence" in outcome:
            outcome["requested_evidence"] = [_without(row, "preferred_providers")
                                             for row in outcome["requested_evidence"]]
        if "fact_indexes" in outcome:
            outcome["fact_indexes"] = list(dict.fromkeys(indexes[i] for i in outcome["fact_indexes"]))
    context = result.get("user_context")
    if isinstance(context, dict):
        # These are navigation/instruction fields, not business records. Keep
        # dialogue, restrictions, retained approvals and historical observations.
        for key in ("evidence_refs", "knowledge_reuse_contract", "observation_feedback"):
            context.pop(key, None)
        observed = context.get("observed_execution")
        if isinstance(observed, dict):
            observed.pop("contract", None)
            observed.pop("detail_contract", None)
            for outcome in observed.get("outcomes", ()):
                task_input = outcome.pop("task_input", None)
                if isinstance(task_input, dict):
                    outcome["observation_scope"] = _without(task_input, "control_mode")
                for key in ("assignment_issue", "owner_agent"):
                    outcome.pop(key, None)
    return result


def _without(row, *names):
    return {key: deepcopy(value) for key, value in row.items() if key not in names}


def _action(row):
    result = _without(row, "operation_key", "work_item_id", "approval_binding",
                      "approval_id", "argument_bindings", "control_id")
    if "arguments_reference" in result:
        result["arguments_reference"] = _preview(result["arguments_reference"])
    return result


def _fact(row):
    result = _without(row, "source_ref", "producer_id", "producer_version")
    if "value_reference" in result:
        result["value_reference"] = _preview(result["value_reference"])
    return result


def _preview(row):
    # The composer cannot call readers. Preserve incompleteness, not navigation.
    return _without(row, "tool", "arguments", "reference")


def _receipt(row):
    result = _without(row, "receipt_id", "operation_key")
    if result.get("action"):
        result["action"] = _action(result["action"])
    return result


def _observation(entry):
    result = _without(entry, "publication_id", "observation_id")
    if "observation" not in entry:
        return result  # INVALID entries are not empty successful observations.
    observation = deepcopy(entry["observation"])
    observation["facts"] = [_fact(row) for row in observation.get("facts", ())]
    observation["receipts"] = [_receipt(row) for row in observation.get("receipts", ())]
    observation["write_recovery"] = []
    for row in entry["observation"].get("write_recovery", ()):
        recovery = _without(row, "operation_key", "source_ref")
        if "detail_reference" in recovery:
            recovery["detail_reference"] = _preview(recovery["detail_reference"])
        observation["write_recovery"].append(recovery)
    result["observation"] = observation
    return result


def composition_evidence(snapshot):
    """Preserve scope, values, temporal state and explicitly public sources."""
    result = deepcopy(snapshot)
    result["facts"] = [_fact(row) for row in snapshot.get("facts", ())]
    result["receipts"] = [_receipt(row) for row in snapshot.get("receipts", ())]
    result["pending_actions"] = [_action(row) for row in snapshot.get("pending_actions", ())]
    receipt_indexes = {row["receipt_id"]: index for index, row in enumerate(snapshot.get("receipts", ()))
                       if "receipt_id" in row}
    result["outcomes"] = []
    for row in snapshot.get("outcomes", ()):
        outcome = _without(row, "receipt_ids", "control")
        outcome["receipt_indexes"] = [receipt_indexes[ref] for ref in row.get("receipt_ids", ())
                                       if ref in receipt_indexes]
        result["outcomes"].append(outcome)
    context = result.get("user_context")
    if isinstance(context, dict):
        context.pop("evidence_refs", None)
        if context.get("summary"):
            context["summary"] = _without(context["summary"], "source_ref", "producer_version")
        if "recent_messages" in context:
            context["recent_messages"] = [_without(row, "source_ref") for row in context["recent_messages"]]
        if "business_observations" in context:
            context["business_observations"] = [_observation(row) for row in context["business_observations"]]
        observed = context.get("observed_execution")
        if observed:
            observed["facts"] = [_fact(row) for row in observed.get("facts", ())]
        if "knowledge_evidence" in context:
            context["knowledge_evidence"] = [_without(row, "publication_id") for row in context["knowledge_evidence"]]
        retained = context.get("retained_approval")
        if isinstance(retained, dict):
            retained["operations"] = [_action(row) for row in retained.get("operations", ())]
    from application.conversation_evidence import reusable_evidence
    from application.knowledge_tool_contract import model_evidence
    knowledge = [row["value"] for row in result["facts"]
                 if row.get("requirement_id") == "knowledge.active_source"]
    knowledge.extend(model_evidence(entry["pack"]) for entry in reusable_evidence(context))
    result["public_citations"] = list({entry["evidence_id"]: {
        "evidence_id": entry["evidence_id"], "title": entry.get("title", "")}
        for pack in knowledge for entry in pack.get("evidence", ())}.values())
    return result
