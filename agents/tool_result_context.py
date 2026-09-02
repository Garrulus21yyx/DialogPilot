"""Structured, locator-preserving tool-result context projection."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from core.token_estimator import TokenEstimator
from mcp.tool_manager import ToolResult


SCHEMA_VERSION = "tool-result-context-v1"


def render_tool_result_context(
    result: ToolResult,
    *,
    result_locator: str = "",
    excerpt_tokens: int = 96,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "call_id": result.call_id,
        "tool_name": result.tool_name,
        "status": result.status,
        "success": result.success,
        "effect_status": result.effect_status,
        "receipt_id": result.receipt_id,
        "authority": result.authority,
        "output_schema_version": result.output_schema_version,
        "receipt_schema_version": result.receipt_schema_version,
        "result_locator": result_locator,
        "result_excerpt": TokenEstimator.truncate(
            result.output_for_model, excerpt_tokens,
        ),
        "compacted": False,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class ToolResultContextCompactor:
    """Rebuild provider messages while keeping checkpoint messages immutable."""

    def compact(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        preserve_latest_excerpt: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        copied = [self._copy_message(item) for item in messages]
        result_turns = [
            index for index, message in enumerate(copied)
            if self._has_result(message)
        ]
        latest = result_turns[-1] if result_turns else -1
        compacted = 0
        for index in result_turns:
            if preserve_latest_excerpt and index == latest:
                continue
            for block in copied[index]["content"]:
                if block.get("type") != "tool_result":
                    continue
                payload = self._payload(block.get("content"))
                if payload is None or not payload.get("result_excerpt"):
                    continue
                payload["result_excerpt"] = ""
                payload["compacted"] = True
                block["content"] = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True,
                )
                compacted += 1
        return copied, compacted

    @staticmethod
    def _copy_message(message: Mapping[str, Any]) -> dict[str, Any]:
        copied = dict(message)
        content = copied.get("content")
        if isinstance(content, list):
            copied["content"] = [
                dict(block) if isinstance(block, Mapping) else block
                for block in content
            ]
        return copied

    @staticmethod
    def _has_result(message: Mapping[str, Any]) -> bool:
        return isinstance(message.get("content"), list) and any(
            isinstance(block, Mapping) and block.get("type") == "tool_result"
            for block in message["content"]
        )

    @staticmethod
    def _payload(content: Any) -> dict[str, Any] | None:
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return None
        return (
            payload if isinstance(payload, dict)
            and payload.get("schema_version") == SCHEMA_VERSION else None
        )
