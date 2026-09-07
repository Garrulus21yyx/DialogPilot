"""Knowledge tool's domain outcomes; transport completion is not evidence."""
from __future__ import annotations

from collections.abc import Mapping
from application.agent_result import AgentResultStatus
from application.sales_channels import sales_channel_schema, require_catalog_channel


def knowledge_outcome(data):
    if not isinstance(data, Mapping):
        return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_CONTRACT"
    status = data.get("status")
    if not isinstance(status, str):
        return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_CONTRACT"
    if status == "OK":
        try:
            evidence_items(data)
        except (ValueError, KeyError, TypeError):
            return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_EVIDENCE"
        return AgentResultStatus.SUCCEEDED, "KNOWLEDGE_EVIDENCE_AVAILABLE"
    if data.get("evidence_pack") is not None:
        return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_CONTRACT"
    if status in {"NO_EVIDENCE", "AMBIGUOUS"}:
        return AgentResultStatus.BLOCKED, f"KNOWLEDGE_{status}"
    if status == "UNAVAILABLE":
        return AgentResultStatus.RETRYABLE_FAILURE, "KNOWLEDGE_UNAVAILABLE"
    if status in {"INVALID_CONTRACT", "CONFLICT"}:
        return AgentResultStatus.TERMINAL_FAILURE, f"KNOWLEDGE_{status}"
    return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_CONTRACT"


def evidence_items(data):
    """Validate the wire evidence boundary without inventing source facts."""
    pack = data["evidence_pack"]
    if data.get("status") != "OK" or not isinstance(pack, Mapping):
        raise ValueError("valid evidence pack required")
    if not pack.get("query") or not pack.get("index_manifest_fingerprint"):
        raise ValueError("evidence identity required")
    items = pack["items"]
    if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= 20:
        raise ValueError("bounded evidence required")
    ids = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("evidence item must be an object")
        ref = item["source_ref"]
        if not isinstance(ref, Mapping) or not isinstance(item.get("text"), str):
            raise ValueError("evidence text and reference required")
        if not isinstance(item.get("chunk_id"), str):
            raise ValueError("evidence ID must be text")
        if any(isinstance(ref.get(k), bool) or not isinstance(ref.get(k), int) for k in ("start_char", "end_char")):
            raise ValueError("source offsets must be integers")
        if not item["chunk_id"] or item["chunk_id"] in ids:
            raise ValueError("unique evidence IDs required")
        ids.add(item["chunk_id"])
        if not item["text"].strip() or ref["end_char"] - ref["start_char"] != len(item["text"]):
            raise ValueError("evidence source interval mismatch")
        if ref["start_char"] < 0 or ref["scope"] != "public":
            raise ValueError("unsupported evidence scope")
        if any(not isinstance(ref.get(k), str) or not ref[k] for k in ("source_id", "source_revision", "checksum")):
            raise ValueError("source provenance must be text")
        if len(ref["checksum"]) != 64 or any(c not in "0123456789abcdef" for c in ref["checksum"]):
            raise ValueError("source provenance required")
    return tuple(items)


def tool_domain_outcome(result):
    if result.authority == "knowledge.active_source" and result.success:
        return knowledge_outcome(result.data)
    return None


def validate_answer_citations(text: str, allowed_ids) -> None:
    """Evidence-backed answers use at least one exact supplied citation ID.

    Capture malformed E-markers too: a parser that extracts only valid IDs
    silently treats [E] or [E:...] as if no citation had been emitted.
    """
    import re
    allowed = set(allowed_ids)
    found = re.findall(r"\[([Ee][^\]\r\n]*)\]", text)
    markers = set(found)
    malformed = len(re.findall(r"\[[Ee]", text)) != len(found)
    if malformed or markers - allowed or (allowed and not markers):
        raise ValueError('answer citations do not match supplied evidence')


def evidence_id(chunk_id: str) -> str:
    import hashlib
    return 'E' + hashlib.sha256(chunk_id.encode()).hexdigest()[:12]


def model_evidence(data) -> dict:
    """A complete evidence view; diagnostics stay in the runtime artifact."""
    outcome, reason = knowledge_outcome(data)
    if outcome is not AgentResultStatus.SUCCEEDED:
        return {'status': data.get('status') if isinstance(data, Mapping) else 'INVALID_CONTRACT',
                'evidence': [], 'detail_code': reason}
    return {'status': 'OK', 'query_used': data['evidence_pack']['query'],
            'evidence': [{'evidence_id': evidence_id(item['chunk_id']),
                          'title': item.get('title', ''), 'text': item['text'],
                          'source': dict(item['source_ref'])} for item in evidence_items(data)]}


def knowledge_artifact(artifact) -> bool:
    return (isinstance(artifact, Mapping) and artifact.get('schema') == 'tool-result-v1'
            and isinstance(artifact.get('result'), Mapping)
            and artifact['result'].get('authority') == 'knowledge.active_source')


_KNOWLEDGE_OPTION_KEYS = frozenset({'policy_date', 'as_of', 'applicable_region', 'sales_channel', 'applicable_product'})
_KNOWLEDGE_OPTION_MAX_LENGTH = 128


def knowledge_query_options_schema(contract=None):
    """Wire shape; temporal interpretation remains in knowledge_query_options."""
    schema = {'type': 'object', 'additionalProperties': False, 'properties': {
        key: {'type': 'string', 'minLength': 1, 'pattern': r'\S',
              'maxLength': _KNOWLEDGE_OPTION_MAX_LENGTH}
        for key in sorted(_KNOWLEDGE_OPTION_KEYS)
    }}
    schema['properties']['policy_date'].update(pattern=r'^\d{4}-\d{2}-\d{2}$', description='用户明确指定的政策日历日期 YYYY-MM-DD；系统按知识库业务时区解释，不补时间点。')
    schema['properties']['as_of'].update(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$', description='明确的带时区时间点；仅有日期用 policy_date。')
    channel_schema = sales_channel_schema(contract)
    if channel_schema is not None:
        schema['properties']['sales_channel'] = channel_schema
    else:
        del schema['properties']['sales_channel']
    schema['properties']['applicable_region']['description'] = '仅填写用户明确提供或可信业务上下文确认的适用地区ID；未知省略。'
    schema['properties']['applicable_product']['description'] = '仅填写已确认且与知识来源标注一致的商品范围ID；商品显示名称不等于范围ID，未知省略。'
    schema['not'] = {'required': ['as_of', 'policy_date']}
    return schema


def knowledge_query_options(values, contract=None):
    """Business applicability, distinct from runtime identity and authorization."""
    from datetime import date, datetime
    import re
    from collections.abc import Mapping
    if not isinstance(values, Mapping):
        raise ValueError("knowledge options must be an object")
    if set(values) - _KNOWLEDGE_OPTION_KEYS:
        raise ValueError("unsupported knowledge option")
    if "as_of" in values and "policy_date" in values:
        raise ValueError("choose a date or an instant")
    result = {}
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip() or len(value) > _KNOWLEDGE_OPTION_MAX_LENGTH:
            raise ValueError("knowledge options require bounded strings")
        if key in {'as_of', 'policy_date'} and not re.fullmatch(knowledge_query_options_schema()['properties'][key]['pattern'], value):
            raise ValueError('invalid temporal format')
        if key == 'sales_channel':
            result[key] = require_catalog_channel(value, contract)
        elif key == 'policy_date':
            result[key] = date.fromisoformat(value).isoformat()
        elif key == 'as_of':
            instant = datetime.fromisoformat(value)
            if instant.utcoffset() is None:
                raise ValueError("knowledge as_of requires timezone")
            result[key] = instant.isoformat()
        else:
            result[key] = value.strip()
    return result


def knowledge_query_schema(contract=None):
    """The shared Agent/direct tool input contract; runtime owns budgets and ACL."""
    options = knowledge_query_options_schema(contract)
    return {**options, 'required': ['query'], 'properties': {
        'query': {'type': 'string', 'minLength': 1, 'maxLength': 4000, 'pattern': r'\S'},
        **options['properties'],
        'source_types': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string'}},
        'regions': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string'}},
    }}


def knowledge_time_window(options, *, current, business_timezone, contract=None):
    """Resolve explicit dates using host-owned zone; preserve DST day length."""
    from datetime import date, datetime, time, timedelta, timezone
    from zoneinfo import ZoneInfo
    options = knowledge_query_options(options, contract)
    if 'policy_date' in options:
        if not business_timezone:
            raise ValueError('knowledge business timezone required for calendar dates')
        zone = ZoneInfo(business_timezone)
        day = date.fromisoformat(options['policy_date'])
        start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
        try:
            end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        except OverflowError as exc:
            raise ValueError("calendar date exceeds supported datetime range") from exc
        if end <= start:
            raise ValueError('unsupported calendar date')
        return start, end
    instant = datetime.fromisoformat(options['as_of']) if 'as_of' in options else current
    if not isinstance(instant, datetime) or instant.utcoffset() is None:
        raise ValueError('trusted current instant required')
    return instant, None


def knowledge_tool_schema_for_context(context):
    return knowledge_query_schema(context.get("knowledge_filter_contract"))
