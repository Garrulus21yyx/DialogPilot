"""Per-call review projection; never edits persisted SDK messages or evidence."""
from copy import deepcopy
from decimal import Decimal
import json
from math import isfinite


def project_review_context(payload):
    value = deepcopy(payload)
    # Preserve schemas too: parameter descriptions/enums can carry business
    # meaning not repeated in the tool description. Only remove exact duplicates.
    history = value.get("working_context", [])
    candidate = value.get("candidate")
    if history and history[-1].get("role") == "ai":
        last = history[-1]
        if value.get("proposed_outcome") == "COMPLETE" and last.get("content") == candidate:
            last.pop("content")
            last["content_ref"] = "candidate"
        elif isinstance(candidate, dict) and "tool" in candidate:
            for call in last.get("tool_calls", ()):
                if call.get("name") == candidate["tool"] and call.get("args") == candidate.get("arguments"):
                    call.pop("args")
                    call["arguments_ref"] = "candidate.arguments"
    for message in history:
        if isinstance(message.get("content"), str):
            _decode_json_text(message, "content")
        elif isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    _decode_json_text(block, "text")
    return value


def _decode_json_text(value, key):
    """Unwrap JSON text once, without parsing arbitrary business string fields."""
    if not isinstance(value.get(key), str) or key + "_json" in value:
        return
    try:
        body = json.loads(value[key], object_pairs_hook=_unique_object,
                          parse_float=_exact_float, parse_constant=_keep_original)
        json.dumps(body, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError, ArithmeticError):
        return
    if isinstance(body, (dict, list)):
        value.pop(key)
        value[key + "_json"] = body


def _keep_original(_value):
    # Decimal lexemes and non-finite values must not be rounded by projection.
    raise ValueError("retain_original_json_text")


def _exact_float(text):
    value = float(text)
    if not isfinite(value) or Decimal(str(value)) != Decimal(text):
        raise ValueError("retain_original_decimal")
    return value


def _unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("retain_duplicate_json_keys")
    return value
