"""Versioned, role-separated input shared by encoder training and inference."""
from __future__ import annotations

from dataclasses import dataclass
import json
import unicodedata


CONTEXT_INPUT_SCHEMA = "dialogpilot-encoder-context-v1"
MAX_RECENT_MESSAGES = 6


@dataclass(frozen=True)
class EncoderInput:
    text: str
    messages: tuple[tuple[str, str], ...] = ()
    objectives: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("encoder current text is required")
        if any(role not in {"user", "assistant"} or not isinstance(text, str)
               or not text.strip() for role, text in self.messages):
            raise ValueError("encoder history requires user/assistant text")
        if any(not isinstance(value, str) or not value.strip() for value in self.objectives):
            raise ValueError("encoder objectives must be nonempty text")
        object.__setattr__(self, "messages", tuple(self.messages[-MAX_RECENT_MESSAGES:]))
        object.__setattr__(self, "objectives", tuple(self.objectives))

    @classmethod
    def from_turn(cls, observations, state, context):
        return cls(observations.raw_text,
                   tuple((message.role, message.content) for message in context.recent_messages
                         if message.role in {"user", "assistant"}) if context else (),
                   tuple(control.objective for control in state.active_work_controls))

    @classmethod
    def from_record(cls, record):
        return cls(record["text"], tuple((item["role"], item["content"])
                                         for item in record.get("messages", ())),
                   tuple(record.get("objectives", ())))

    def identity(self):
        def normalize(text):
            return "".join(unicodedata.normalize("NFKC", text).lower().split())
        return json.dumps((normalize(self.text), tuple((role, normalize(text)) for role, text in self.messages),
                           tuple(normalize(text) for text in self.objectives)),
                          ensure_ascii=False, separators=(",", ":"))

    def channels(self):
        # Current text retains its own feature space; assistant keywords do not
        # become indistinguishable from the user's current request.
        yield "current", self.text
        for distance, (role, text) in enumerate(reversed(self.messages), 1):
            yield f"history:{distance}:{role}", text
        for objective in self.objectives:
            yield "objective", objective
