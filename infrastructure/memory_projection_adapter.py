"""Production adapters from canonical PostgreSQL events to legacy Memory stores."""
from __future__ import annotations

import asyncio
from datetime import timezone

from application.conversation_projection import (
    ConversationSubject,
    ProjectableConversationEvent,
    ProjectionApplyStatus,
    ProjectionName,
)
from memory.conversation_memory import Message, MsgRole


class MemoryProjectionContractError(RuntimeError):
    pass


class PostgresLegacyMemoryProjectionAdapter:
    """One target-specific adapter over MemoryManager's existing store owners."""

    def __init__(self, pool, memory, projection_name: ProjectionName):
        self.pool = pool
        self.memory = memory
        self.projection_name = projection_name

    def apply(self, event: ProjectableConversationEvent) -> ProjectionApplyStatus:
        if event.projection_name is not self.projection_name:
            raise MemoryProjectionContractError("projection adapter target changed")
        turn = self._turn(event)
        if turn is None:
            # Fact extraction is selected only by final-response completion.
            if self.projection_name is ProjectionName.FACT_EXTRACTION:
                return ProjectionApplyStatus.ALREADY_APPLIED
            raise MemoryProjectionContractError(
                f"projectable event lacks canonical turn: {event.event_type}"
            )
        role = MsgRole.USER if turn[1] == "inbound" else MsgRole.ASSISTANT
        message = Message(
            role=role,
            content=turn[2],
            timestamp=turn[4].astimezone(timezone.utc),
            metadata={
                **dict(turn[5] or {}),
                "canonical_turn_key": turn[0],
                "canonical_event_id": event.event_id,
            },
            message_id=turn[3],
            seq=int(turn[6]),
        )
        event_key = f"{event.event_id}:e{event.source_deletion_epoch}"
        applied = asyncio.run(self._apply(event, message, event_key))
        return (
            ProjectionApplyStatus.APPLIED
            if applied else ProjectionApplyStatus.ALREADY_APPLIED
        )

    def delete_subject(
        self,
        subject: ConversationSubject,
        *,
        through_deletion_epoch: int,
    ) -> None:
        del through_deletion_epoch
        asyncio.run(self.memory.delete_conversation_projection(
            subject.user_id, subject.conversation_id,
        ))

    async def _apply(
        self,
        event: ProjectableConversationEvent,
        message: Message,
        event_key: str,
    ) -> bool:
        subject = event.subject
        if self.projection_name is ProjectionName.WORKING_WINDOW:
            return await self.memory.project_working_message(
                subject.user_id, subject.conversation_id, message,
                event_key=event_key,
            )
        if self.projection_name is ProjectionName.THREAD_SUMMARY:
            return await self.memory.project_thread_summary(
                subject.user_id, subject.conversation_id, event_key=event_key,
            )
        if self.projection_name is ProjectionName.EPISODIC_INDEX:
            return await self.memory.project_episodic_message(
                subject.user_id, subject.conversation_id, message,
                event_key=event_key,
            )
        if self.projection_name is ProjectionName.FACT_EXTRACTION:
            if event.event_type not in {
                "FINAL_RESPONSE_SELECTED", "LEGACY_FINAL_RESPONSE_IMPORTED",
            }:
                return False
            return await self.memory.project_fact_schedule(
                subject.user_id, subject.conversation_id, event_key=event_key,
            )
        raise MemoryProjectionContractError("unsupported Memory projection target")

    def _turn(self, event: ProjectableConversationEvent):
        turn_key = str(
            event.payload.get("inbound_turn_key")
            or event.payload.get("outbound_turn_key")
            or ""
        )
        if not turn_key:
            return None
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT turn_key, role, content, turn_id, created_at, metadata, seq
                FROM dialogpilot_app.conversation_turns
                WHERE turn_key=%s AND tenant_id=%s AND user_id=%s
                  AND conversation_id=%s
            """, (
                turn_key, event.subject.tenant_id, event.subject.user_id,
                event.subject.conversation_id,
            )).fetchone()
        if row is None:
            raise MemoryProjectionContractError("canonical turn is unavailable")
        return row
