import copy
import json

import pytest

from infrastructure.domain_review_context import project_review_context
from scripts.measure_domain_review_context import audit, payloads


@pytest.mark.parametrize("kind", ["COMPLETE", "PREPARE_ACTION", "NEEDS_USER_INPUT"])
@pytest.mark.parametrize("size", [0, 1, 7, 100])
def test_projection_preserves_every_value_and_source_without_mutating_input(kind, size):
    candidate = "All done." if kind == "COMPLETE" else {"tool": "prepare", "arguments": {"ids": list(range(size))}}
    payload = {"proposed_outcome": kind, "candidate": candidate,
        "capabilities": [{"name": "prepare", "description": "Effect", "schema": {
            "type": "object", "properties": {"mode": {"enum": ["read", "write"],
            "description": "Never write without confirmation"}}}}],
        "business_policy": "Only inspect; do not submit.", "receipts": [{"amount": -1346}],
        "working_context": [
            {"role": "human", "content": [{"type": "text", "text": json.dumps({"ids": list(range(size))})}]},
            {"role": "tool", "content": json.dumps({"ids": list(range(size)), "text": "  preserve spaces  "}),
             "tool_call_id": "t"},
            {"role": "ai", "content": candidate if kind == "COMPLETE" else "",
             "tool_calls": [] if kind == "COMPLETE" else [{"id": "p", "name": "prepare", "args": candidate["arguments"]}]}]}
    original = copy.deepcopy(payload)
    assert audit(payload)["preservation_passed"]
    projected = project_review_context(payload)
    assert project_review_context(projected) == projected
    assert payload == original
    assert projected["capabilities"] == payload["capabilities"]
    assert projected["working_context"][1]["content_json"]["text"] == "  preserve spaces  "


def test_plain_text_and_distinct_tool_arguments_are_not_elided():
    payload = {"candidate": {"tool": "x", "arguments": {"id": 2}}, "working_context": [
        {"role": "tool", "content": "not JSON"},
        {"role": "ai", "content": "keep", "tool_calls": [{"name": "x", "args": {"id": 1}}]}]}
    assert project_review_context(payload) == payload


def test_capture_decoder_supports_cli_body_without_treating_no_data_as_success():
    p = {"working_context": [], "capabilities": [], "proposed_outcome": "COMPLETE"}
    assert list(payloads({"body": {"data": [{"input": [{"content": json.dumps(p)}]}]}})) == [p]


@pytest.mark.parametrize("text", ['{"amount":1,"amount":2}',
    '{"value":0.12345678901234567890123456789}', '{"value":1e400}', '{"value":NaN}',
    r'{"s":"\ud800"}', r'{"\ud800":{"nested":"\udfff"}}',
    '{"value":1e99999999999999999999999999999999999999999}',
    '{"value":1e-99999999999999999999999999999999999999999}'])
def test_numeric_lexemes_and_duplicate_keys_are_kept_verbatim(text):
    payload = {"working_context": [{"role": "tool", "content": text}]}
    assert project_review_context(payload) == payload


def test_json_answer_appears_once_before_decoding():
    answer = '{"amount":12}'
    result = project_review_context({"candidate": answer, "proposed_outcome": "COMPLETE",
        "working_context": [{"role": "ai", "content": answer}]})
    assert result['working_context'] == [{"role": "ai", "content_ref": "candidate"}]
