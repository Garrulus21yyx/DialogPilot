"""Execute the enabled command-primary read-only work shapes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from application.command_primary_media import media_policy_for
from application.command_primary_result import (
    media_read_execution_result,
    read_only_execution_result,
)
from application.media_read_work import execute_media_read_work
from application.read_only_work import execute_read_only_work
from memory.context import ContextSection


@dataclass(frozen=True)
class CommandPrimaryWorkOutcome:
    result: Any
    succeeded: bool


async def execute_command_primary_work(
    request_id: str,
    chat_plan: Any,
    *,
    tool_manager: Any,
    identity: Any,
    media: ContextSection | None,
) -> CommandPrimaryWorkOutcome:
    plan = chat_plan.plan
    if media_policy_for(plan) is not None:
        execution = execute_media_read_work(plan, media)
        return CommandPrimaryWorkOutcome(
            media_read_execution_result(request_id, chat_plan, execution),
            True,
        )
    execution = await execute_read_only_work(
        plan, tool_manager, identity, chat_plan.requirements
    )
    return CommandPrimaryWorkOutcome(
        read_only_execution_result(request_id, chat_plan, execution),
        execution.succeeded,
    )
