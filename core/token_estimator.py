"""Deterministic provider-independent token estimation primitives."""
from __future__ import annotations

import json
import math
from typing import Any, Iterable, Mapping


class TokenEstimator:
    """Fast conservative estimator used only for pre-provider budgeting."""

    @staticmethod
    def estimate(value: Any) -> int:
        if value is None:
            return 0
        text = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True,
        )
        units = 0.0
        for char in text:
            code = ord(char)
            units += 2 / 3 if 0x3400 <= code <= 0x9FFF else 1 / 4
        return max(1, math.ceil(units)) if text else 0

    @classmethod
    def estimate_messages(cls, messages: Iterable[Mapping[str, Any]]) -> int:
        total = 0
        for message in messages:
            total += 4
            total += cls.estimate(message.get("content", ""))
            total += cls.estimate(message.get("tool_calls"))
            total += cls.estimate(message.get("tool_call_id"))
        return total

    @classmethod
    def truncate(cls, text: str, max_tokens: int) -> str:
        text = str(text or "")
        if max_tokens <= 0:
            return ""
        if cls.estimate(text) <= max_tokens:
            return text
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if cls.estimate(text[:mid]) <= max_tokens:
                low = mid
            else:
                high = mid - 1
        clipped = text[:low].rstrip()
        return f"{clipped}…" if clipped else ""
