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


async def review_evidence_messages(messages, context, archive):
    """Build a reviewer-only view from originals or explicit successful pages."""
    from langchain_core.messages import AIMessage, ToolMessage
    def decoded(text):
        try:
            value = json.loads(text, object_pairs_hook=_unique_object,
                               parse_float=_exact_float, parse_constant=_keep_original)
            json.dumps(value, ensure_ascii=False).encode('utf-8')
            return value
        except (ValueError, TypeError, UnicodeError, ArithmeticError):
            return None
    pages = {m.tool_call_id: decoded(m.content) for m in messages
             if isinstance(m, ToolMessage) and m.status != "error"}
    paged = set()
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            page = pages.get(call['id'])
            if (call['name'] == 'read_tool_result' and isinstance(page, dict)
                    and page.get('reference') == call['args'].get('reference')
                    and isinstance(page.get('text'), str) and page['text'].strip()):
                paged.add(page['reference'])
    originals = {}
    async def original_content(ref):
        if archive is None:
            raise ValueError("action_review_evidence_reader_unavailable")
        if ref not in originals:
            originals[ref] = (await archive.load(context, ref))["content"]
        return originals[ref]
    async def hydrate(value):
        if isinstance(value, dict):
            ref = value.get('result_ref')
            if ref and value.get('complete') is False and ref not in paged:
                text = await original_content(ref)
                body = decoded(text)
                return body if body is not None else text
            return {key: await hydrate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [await hydrate(item) for item in value]
        return value
    result = []
    for message in messages:
        pointer = decoded(message.content)
        ref = pointer.get("result_ref") if isinstance(pointer, dict) else None
        if ref and pointer.get("complete") is False and ref not in paged:
            message = message.model_copy(update={"content": await original_content(ref)})
        elif isinstance(message.content, list):
            blocks = deepcopy(message.content)
            for block in blocks:
                if not isinstance(block, dict) or block.get('type') != 'text':
                    continue
                body = decoded(block.get('text'))
                if isinstance(body, dict) and 'runtime_context' in body:
                    # Only application-owned supplied facts, not quoted customer text.
                    facts = body['runtime_context'].get('verified_facts', [])
                    revised = await hydrate(facts)
                    if revised != facts:
                        body['runtime_context']['verified_facts'] = revised
                        block['text'] = json.dumps(body, ensure_ascii=False)
            message = message.model_copy(update={"content": blocks})
        result.append(message)
    return result
