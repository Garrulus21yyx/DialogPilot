"""Typed pre-release route outcomes compatible with target public/domain schemas."""
from __future__ import annotations

from dataclasses import dataclass


class RouteOutcomeContractError(ValueError):
    pass


def _nonblank(items: tuple[str, ...]) -> bool:
    return all(str(item).strip() for item in items)


def _validate_tuple(name: str, items: tuple[str, ...]) -> None:
    if not isinstance(items, tuple):
        raise RouteOutcomeContractError(f"{name} must be an immutable tuple")
    if not _nonblank(items):
        raise RouteOutcomeContractError(f"{name} contains blank values")
    if len(set(items)) != len(items):
        raise RouteOutcomeContractError(f"{name} contains duplicate values")


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
        _validate_tuple("missing_inputs", self.missing_inputs)
        if not self.missing_inputs:
            raise RouteOutcomeContractError("NeedsInput must name every missing input")
        if self.kind != "user_input":
            raise RouteOutcomeContractError("Clarify only supports user_input signals")
        if self.schema_version != "needs-input-v1":
            raise RouteOutcomeContractError("unsupported NeedsInput schema version")
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
        for name in (
            "reason_codes", "verified_facts", "user_assertions", "actions_attempted",
            "action_receipts", "missing_materials", "media_evidence",
            "commitments_and_sla",
        ):
            _validate_tuple(name, getattr(self, name))
        if self.risk not in {"low", "medium", "high", "critical"}:
            raise RouteOutcomeContractError("Handoff draft risk is unsupported")
        if self.schema_version != "handoff-contract-v1":
            raise RouteOutcomeContractError("unsupported Handoff schema version")
        if self.release_status != "draft_only":
            raise RouteOutcomeContractError(
                "M2 Handoff contract cannot claim released/handed-off state"
            )


RouteOutcomePayload = NeedsInputDraft | HandoffContractDraft | None
