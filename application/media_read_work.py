"""Consume L1 media evidence for the bounded read-only media action."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agents.orchestration_contracts import AgentType, TaskEffect
from application.media_requirement import MediaStage
from application.turn_plan import TurnPlan
from memory.context import ContextSection


class MediaReadWorkError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaReadWorkExecution:
    task_id: str
    owner: AgentType
    response: str
    coverage: dict[str, Any]


def execute_media_read_work(
    plan: TurnPlan,
    media: ContextSection | None,
) -> MediaReadWorkExecution:
    work = plan.work
    if work is None or len(work.items) != 1 or len(work.graph.tasks) != 1:
        raise MediaReadWorkError("media read requires one work item")
    item = work.items[0]
    task = work.graph.tasks[0]
    policy = item.media_policy
    if (
        task.effect is not TaskEffect.READ_ONLY
        or item.allowed_tools
        or policy is None
        or policy.requirement.minimum_stage is not MediaStage.L1_TEXT_EXTRACTION
    ):
        raise MediaReadWorkError("work item is outside the L1 read shape")
    if media is None or media.tag != "media_observations":
        raise MediaReadWorkError("L1 media evidence is unavailable")

    observations = json.loads(media.content)
    texts = tuple(dict.fromkeys(
        str(item["text"]).strip()
        for item in observations
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ))
    artifact_refs = tuple(dict.fromkeys(
        str(item.get("parse_result_id") or item.get("evidence_id") or "")
        for item in observations
        if isinstance(item, dict)
        and str(item.get("parse_result_id") or item.get("evidence_id") or "")
    ))
    if not texts or not artifact_refs:
        raise MediaReadWorkError("L1 media evidence contains no readable text")
    return MediaReadWorkExecution(
        task_id=item.task_id,
        owner=task.owner,
        response="识别到的文字：" + "\n".join(texts),
        coverage={
            "complete": True,
            "media_requirement_id": policy.requirement.requirement_id,
            "media_artifact_refs": list(artifact_refs),
        },
    )
