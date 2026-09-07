"""Export privacy preserves the algebra of business evidence, not just one SKU."""
import copy
import json

import pytest

from core.telemetry_privacy import mask_telemetry
from infrastructure.langfuse_trace_sink import mask_observation


@pytest.mark.parametrize("depth", [0, 1, 3])
@pytest.mark.parametrize("count", [1, 2, 40])
def test_encoded_and_structured_business_evidence_preserves_keys_values_and_input(depth, count):
    data = {"variants": {str(7706410293 + i): {"item_id": str(7706410293 + i),
             "price": 249.01 + i, "options": {"color": "black"}} for i in range(count)},
            "observed_at": "2026-09-07T12:27:31+00:00", "amount": -16.63,
            "operation_key": "action:v1:65239c0067a70cbd621e6346bdeb5d29a",
            "usage": {"prompt_tokens": 4500, "completion_tokens": 128,
                      "prompt_tokens_details": {"cached_tokens": 2300}},
            "thinking": {"type": "disabled"}, "email": "customer@example.com",
            "password": "do-not-export", "phone": "7706410293"}
    expected = copy.deepcopy(data)
    for key in ("email", "password", "phone"):
        expected[key] = "[REDACTED]"
    source = data
    for _ in range(depth):
        source = {"content": json.dumps(source)}
    original = copy.deepcopy(source)
    result = mask_observation(data=source)
    assert source == original
    for _ in range(depth):
        result = json.loads(result["content"])
    assert result == expected


def test_mature_detectors_mask_scoped_text_without_numeric_heuristics():
    import jwt
    token = jwt.encode({"sub": "someone"}, "test-key-long-enough-for-this-test", algorithm="HS256")
    text = f"email=a@example.com password=secret-value Bearer bearer-value {token} SKU 7706410293 price -16.63"
    result = mask_telemetry(text)
    for secret in ("a@example.com", "secret-value", "bearer-value", token):
        assert secret not in result
    assert "7706410293" in result and "-16.63" in result


def test_reasoning_is_removed_but_visible_response_and_config_are_preserved():
    source = {"output": json.dumps({"content": [
        {"type": "thinking", "thinking": "private scratchpad", "signature": "private-signature"},
        {"type": "text", "text": "The exchange was requested."}]}),
        "thinking": {"type": "disabled"}, "reasoning_content": "private-other"}
    result = mask_telemetry(source)
    assert "private" not in json.dumps(result)
    assert "The exchange was requested." in result["output"]
    assert result["thinking"] == source["thinking"]


def test_known_secret_replacement_is_scoped_to_values_not_dictionary_keys():
    source = {"business-key": "API returned known-secret", "other": "product 7747408585"}
    result = mask_telemetry(source, secrets=("known-secret",))
    assert tuple(result) == tuple(source)
    assert "known-secret" not in result["business-key"]
    assert result["other"] == source["other"]


@pytest.mark.parametrize("separator", ["=", " = ", ": "])
def test_credential_assignment_and_ordinary_prose(separator):
    text = f"order note: don't alter price=16.63 password{separator}\"private value\""
    masked = mask_telemetry(text)
    assert "private value" not in masked
    assert "don't alter price=16.63" in masked
    assert "[REDACTED:" in masked


def test_decoded_sibling_containers_never_share_a_cached_identity():
    source = {str(i): json.dumps({"result": {"item": str(7000000000+i)}}) for i in range(200)}
    assert mask_telemetry(source) == source


def test_nullable_tool_schema_and_nested_internal_attributes_are_supported():
    from core.tracing import TraceRecorder
    schema = {"type": "object", "properties": {"color": {"type": ["string", "null"]},
        "email": {"type": "string"}, "address": {"type": "object", "properties": {
            "phone": {"type": "string"}}}}}
    assert mask_telemetry(schema) == schema
    result = TraceRecorder._sanitize_attributes({"payload": {"password": "private value", "item_id": "7706410293"}})
    assert "private value" not in str(result)
    assert "7706410293" in str(result)


def test_partial_prose_and_credential_punctuation_do_not_change_business_evidence():
    prose = 'Description: "sale starts'
    assert mask_telemetry(prose) == prose
    assert "abc:def:ghi" not in mask_telemetry('password=abc:def:ghi')
    assert ":def:ghi" not in mask_telemetry('password=abc:def:ghi')


@pytest.mark.parametrize("value", [r'password="abc\"def"', r'password=abc\def'])
def test_credential_detector_replaces_source_spans_not_unescaped_tokens(value):
    assert mask_telemetry(value).startswith("[REDACTED:")
    assert "abc" not in mask_telemetry(value) and "def" not in mask_telemetry(value)


@pytest.mark.parametrize("encoded", [False, True])
def test_schema_declarations_and_ordinary_properties_have_distinct_semantics(encoded):
    schema = {"type": "object", "properties": {"email": {"type": "string"}}}
    payload = {"tool_schema": schema, "properties": {"password": "private-pass", "phone": "4155552671"}}
    result = mask_telemetry(json.dumps(payload) if encoded else payload)
    if encoded:
        result = json.loads(result)
    assert result["tool_schema"] == schema
    assert result["properties"] == {"password": "[REDACTED]", "phone": "[REDACTED]"}


def test_encoded_reasoning_block_is_not_plain_business_content():
    result = mask_telemetry({"content": json.dumps({"type": "reasoning", "content": "private-thought"})})
    assert "private-thought" not in str(result)


@pytest.mark.parametrize("branch", ["allOf", "anyOf", "oneOf", "prefixItems"])
def test_schema_ownership_follows_schema_children_not_examples(branch):
    node = {"type": ["object", "null"], "properties": {"email": {"type": "string"}}}
    schema = {"type": "object", "properties": {"contact": node}, branch: [node],
              "examples": [{"properties": {"password": "example-private"}}]}
    result = mask_telemetry(schema)
    assert result["properties"] == schema["properties"]
    assert result[branch] == schema[branch]
    assert result["examples"] == [{"properties": {"password": "[REDACTED]"}}]
