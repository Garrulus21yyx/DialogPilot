"""Task-owned media requirements compiled from the action registry."""
from __future__ import annotations

from dataclasses import dataclass

from application.media_requirement import MediaRequirementPolicy
from application.routing_media_probe import RoutingMediaScope


@dataclass(frozen=True)
class TaskMediaPolicy:
    requirement: MediaRequirementPolicy
    scope: RoutingMediaScope

