"""Deterministic per-model-call context budgeting without mutating memory."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.token_estimator import TokenEstimator


class ModelContextBudgetExceeded(ValueError):
    def __init__(self, required_tokens: int, available_tokens: int) -> None:
        self.required_tokens = required_tokens
        self.available_tokens = available_tokens
        super().__init__(
            f"model context requires {required_tokens} tokens; "
            f"available input budget is {available_tokens}"
        )


class InvalidToolMessageSequence(ValueError):
    pass


@dataclass(frozen=True)
class ContextBuildReport:
    policy_version: str
    original_tokens: int
    final_tokens: int
    available_tokens: int
    removed_items: tuple[str, ...] = ()
    externalized_items: tuple[str, ...] = ()


@dataclass(frozen=True)
class BudgetedPayload:
    payload: Mapping[str, Any]
    report: ContextBuildReport


@dataclass(frozen=True)
class BudgetedMessages:
    messages: tuple[Mapping[str, Any], ...]
    report: ContextBuildReport


class ContextBudgetManager:
    """Fit one provider input; persistent Transcript/Summary remain untouched."""

    version = "model-context-budget-v1"

    def __init__(
        self,
        *,
        context_window_tokens: int = 16000,
        reserved_output_tokens: int = 1200,
        protocol_reserve_tokens: int = 600,
    ) -> None:
        available = (
            int(context_window_tokens)
            - int(reserved_output_tokens)
            - int(protocol_reserve_tokens)
        )
        if available < 1:
            raise ValueError("model context reserves leave no input budget")
        self.available_tokens = available
        self._estimator = TokenEstimator()

    def fit_payload(
        self,
        payload: Mapping[str, Any],
        *,
        trim_oldest_paths: Sequence[str] = (),
    ) -> BudgetedPayload:
        """Trim declared chronological lists only; never invent summary content."""
        value = copy.deepcopy(dict(payload))
        original = self._estimate(value)
        removed: list[str] = []
        while self._estimate(value) > self.available_tokens:
            changed = False
            for path in trim_oldest_paths:
                items = self._path(value, path)
                if isinstance(items, list) and items:
                    items.pop(0)
                    removed.append(f"{path}[oldest]")
                    changed = True
                    break
            if not changed:
                required = self._estimate(value)
                raise ModelContextBudgetExceeded(
                    required, self.available_tokens,
                )
        final = self._estimate(value)
        return BudgetedPayload(
            value,
            ContextBuildReport(
                self.version, original, final, self.available_tokens,
                tuple(removed), (),
            ),
        )

    def fit_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        protected_tail: int = 1,
        tool_payload_tokens: int = 800,
    ) -> BudgetedMessages:
        """Externalize persisted Tool payloads, then remove oldest atomic rounds."""
        values = [copy.deepcopy(dict(message)) for message in messages]
        original = self._estimate(values)
        externalized: list[str] = []
        for index, message in enumerate(values):
            if message.get("role") != "tool":
                continue
            if self._estimate(message.get("content")) <= tool_payload_tokens:
                continue
            artifact_ref = str(message.get("artifact_ref") or "").strip()
            if not artifact_ref:
                continue
            raw = json.dumps(
                message.get("content"), ensure_ascii=False,
                sort_keys=True, separators=(",", ":"), default=str,
            )
            message["content"] = {
                "externalized": True,
                "artifact_ref": artifact_ref,
                "content_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            }
            externalized.append(f"messages[{index}].content")

        blocks = self._message_blocks(values)
        protected_from = max(0, len(values) - max(0, int(protected_tail)))
        removable = [
            block for block in blocks
            if block[-1] < protected_from
            and not any(values[index].get("role") == "system" for index in block)
        ]
        removed_indices: set[int] = set()
        while self._estimate([
            item for index, item in enumerate(values)
            if index not in removed_indices
        ]) > self.available_tokens and removable:
            removed_indices.update(removable.pop(0))
        fitted = tuple(
            item for index, item in enumerate(values)
            if index not in removed_indices
        )
        final = self._estimate(fitted)
        if final > self.available_tokens:
            raise ModelContextBudgetExceeded(final, self.available_tokens)
        return BudgetedMessages(
            fitted,
            ContextBuildReport(
                self.version, original, final, self.available_tokens,
                tuple(f"messages[{index}]" for index in sorted(removed_indices)),
                tuple(externalized),
            ),
        )

    def _estimate(self, value: Any) -> int:
        return self._estimator.estimate(json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        ))

    @staticmethod
    def _path(value: dict[str, Any], path: str) -> Any:
        current: Any = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _message_blocks(messages: Sequence[Mapping[str, Any]]) -> list[tuple[int, ...]]:
        """Keep assistant tool calls and all of their Tool results atomic."""
        blocks: list[tuple[int, ...]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            calls = message.get("tool_calls") if message.get("role") == "assistant" else None
            if not calls:
                blocks.append((index,))
                index += 1
                continue
            call_ids = {
                str(call.get("id")) for call in calls
                if isinstance(call, Mapping) and call.get("id")
            }
            block = [index]
            cursor = index + 1
            while cursor < len(messages):
                candidate = messages[cursor]
                if (
                    candidate.get("role") == "tool"
                    and str(candidate.get("tool_call_id") or "") in call_ids
                ):
                    block.append(cursor)
                    cursor += 1
                    continue
                break
            responded = {
                str(messages[item].get("tool_call_id")) for item in block[1:]
            }
            if responded != call_ids:
                raise InvalidToolMessageSequence(
                    "assistant tool calls require one matching result each"
                )
            blocks.append(tuple(block))
            index = cursor
        covered = {item for block in blocks for item in block}
        for item, message in enumerate(messages):
            if message.get("role") == "tool" and (
                item not in covered
                or len(blocks[next(
                    idx for idx, block in enumerate(blocks) if item in block
                )]) == 1
            ):
                raise InvalidToolMessageSequence("orphaned tool result")
        return blocks
