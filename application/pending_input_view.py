"""Model-facing field names and descriptions; execution bindings stay private."""
from collections import Counter


def pending_input_fields(pending):
    if "requested_information" in pending and "requested_fields" not in pending:
        raise ValueError("pending_input_requires_authoritative_bindings")
    fields = pending.get("requested_fields", ())
    counts = Counter(item["field_name"] for item in fields)
    reserved = {item["field_name"] for item in fields}
    objectives = {item.get("work_item_id"): item["objective"]
                  for item in pending.get("objectives", ()) if item.get("work_item_id")}
    result = {}
    for index, item in enumerate(fields, 1):
        name = item["field_name"]
        key = name
        if counts[name] > 1:
            key = f"{name}_{index}"
            while key in reserved or key in result:
                key += "_"
        description = f"User's answer for {name}."
        objective = objectives.get(item["target_work_item_id"])
        if objective:
            description += f" Requested for: {objective}"
        result[key] = (item, description)
    return result


def pending_input_context(pending):
    if not pending:
        return pending
    return {"requested_information": [
        {"field": key, "description": description}
        for key, (_, description) in pending_input_fields(pending).items()
    ]}
