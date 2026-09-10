"""Display projection; business values are preserved, provenance stays in audit.

Only known envelope metadata is removed. Never recursively redact business keys
or customer text: an order ID is useful, an internal receipt ID is not a citation.
"""
from copy import deepcopy


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
