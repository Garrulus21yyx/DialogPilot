"""M4-T03 structured tool-result context compaction proofs."""
import json

from agents.tool_result_context import (
    SCHEMA_VERSION,
    ToolResultContextCompactor,
    render_tool_result_context,
)
from mcp.tool_manager import ToolResult


def _result(call_id, output):
    return ToolResult(
        success=True, data={}, tool_name="lookup", call_id=call_id,
        status="success", output_for_model=output,
        effect_status="none", receipt_id=f"receipt-{call_id}",
        authority="OrderService", output_schema_version="order-v1",
        receipt_schema_version="evidence-v1",
    )


def _turn(call_id, output):
    return {
        "role": "user",
        "content": [{
            "type": "tool_result", "tool_use_id": call_id,
            "content": render_tool_result_context(
                _result(call_id, output),
                result_locator=f"checkpoint:tool-result/run/{call_id}",
            ),
            "is_error": False,
        }],
    }


def test_projection_is_bounded_and_preserves_receipt_schema_and_locator():
    payload = json.loads(render_tool_result_context(
        _result("call-1", "large-result " * 10_000),
        result_locator="checkpoint:tool-result/run/call-1",
    ))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["receipt_id"] == "receipt-call-1"
    assert payload["receipt_schema_version"] == "evidence-v1"
    assert payload["result_locator"] == "checkpoint:tool-result/run/call-1"
    assert len(payload["result_excerpt"]) < 500


def test_each_step_rebuild_drops_old_excerpts_without_mutating_checkpoint_messages():
    messages = [
        {"role": "user", "content": "question"},
        _turn("call-1", "first-result " * 100),
        _turn("call-2", "latest-result " * 100),
    ]
    provider_messages, compacted = ToolResultContextCompactor().compact(
        messages, preserve_latest_excerpt=True,
    )
    first = json.loads(provider_messages[1]["content"][0]["content"])
    latest = json.loads(provider_messages[2]["content"][0]["content"])
    original_first = json.loads(messages[1]["content"][0]["content"])
    assert compacted == 1
    assert (first["result_excerpt"], first["compacted"]) == ("", True)
    assert "latest-result" in latest["result_excerpt"]
    assert original_first["result_excerpt"]


def test_emergency_compaction_removes_latest_excerpt_but_keeps_locator_receipt():
    messages = [_turn("call-1", "latest-result " * 100)]
    provider_messages, compacted = ToolResultContextCompactor().compact(
        messages, preserve_latest_excerpt=False,
    )
    payload = json.loads(provider_messages[0]["content"][0]["content"])
    assert compacted == 1
    assert payload["result_excerpt"] == ""
    assert payload["receipt_id"] == "receipt-call-1"
    assert payload["result_locator"].endswith("/call-1")
