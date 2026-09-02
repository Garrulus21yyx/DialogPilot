"""Candidate-only adapter over the existing embedded Memory model client."""
from __future__ import annotations

import asyncio

from application.thread_summary import SummarySourceItem
from memory.conversation_memory import Message, MsgRole


class EmbeddedThreadSummarizerAdapter:
    def __init__(self, memory, *, version: str):
        if not version.strip():
            raise ValueError("embedded summarizer version is required")
        self.memory = memory
        self.version = version

    def summarize(self, items: tuple[SummarySourceItem, ...]) -> str:
        messages = [
            Message(
                role=self._role(item.role),
                content=item.content,
                message_id=item.event_id,
                seq=item.seq,
                metadata={"event_type": item.event_type},
            )
            for item in items if item.content
        ]
        if not messages:
            raise RuntimeError("thread summary range has no summarizable raw messages")
        return asyncio.run(self.memory.summarize_thread_candidate(messages))

    @staticmethod
    def _role(value: str) -> MsgRole:
        try:
            return MsgRole(value)
        except ValueError:
            return MsgRole.SYSTEM
