"""Typed pre-release route outcomes compatible with target public/domain schemas."""
from __future__ import annotations

from dataclasses import dataclass


class RouteOutcomeContractError(ValueError):
    pass


def _nonblank(items: tuple[str, ...]) -> bool:
    return all(str(item).strip() for item in items)


@dataclass(frozen=True)
class NeedsInputDraft:
    workflow_run_id: str
    signal_id: str
    kind: str
    expires_at: str
    interaction_publication_id: str
    missing_inputs: tuple[str, ...]
    prompt: str
    schema_version: str = "needs-input-v1"
    draft_only: bool = True

    def __post_init__(self) -> None:
        if any(not str(value).strip() for value in (
            self.workflow_run_id, self.signal_id, self.kind, self.expires_at,
            self.interaction_publication_id, self.prompt, self.schema_version,
        )):
            raise RouteOutcomeContractError("NeedsInput draft identity/content is required")
        if not self.missing_inputs or not _nonblank(self.missing_inputs):
            raise RouteOutcomeContractError("NeedsInput must name every missing input")
        if not self.draft_only:
            raise RouteOutcomeContractError("M2 NeedsInput projection must remain draft-only")


@dataclass(frozen=True)
class HandoffContractDraft:
    handoff_id: str
    reason_codes: tuple[str, ...]
    target_queue_or_owner: str
    problem_summary: str
    user_goal: str
    verified_facts: tuple[str, ...]
    user_assertions: tuple[str, ...]
    actions_attempted: tuple[str, ...]
    action_receipts: tuple[str, ...]
    missing_materials: tuple[str, ...]
    media_evidence: tuple[str, ...]
    emotion_and_user_request: str
    commitments_and_sla: tuple[str, ...]
    risk: str
    recommended_next_action: str
    schema_version: str = "handoff-contract-v1"
    release_status: str = "draft_only"

    def __post_init__(self) -> None:
        required = (
            self.handoff_id, self.target_queue_or_owner, self.problem_summary,
            self.user_goal, self.emotion_and_user_request, self.risk,
            self.recommended_next_action, self.schema_version,
        )
        if any(not str(value).strip() for value in required):
            raise RouteOutcomeContractError("Handoff draft required fields are missing")
        if not self.reason_codes or not _nonblank(self.reason_codes):
            raise RouteOutcomeContractError("Handoff draft requires reason codes")
        for values in (
            self.verified_facts, self.user_assertions, self.actions_attempted,
            self.action_receipts, self.missing_materials, self.media_evidence,
            self.commitments_and_sla,
        ):
            if not _nonblank(values):
                raise RouteOutcomeContractError("Handoff draft lists contain blank values")
        if self.release_status != "draft_only":
            raise RouteOutcomeContractError(
                "M2 Handoff contract cannot claim released/handed-off state"
            )
