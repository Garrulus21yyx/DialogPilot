"""State-bound resolution and the Intent-to-Conversation planning cascade."""
from __future__ import annotations

from application.deterministic_resolution import ResolutionKind
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    TurnProposal,
)
from application.work_item import ArgumentValue, ControlMode


class StateBoundTargetUnderstanding:
    """Compile only facts already bound by authoritative conversation state.

    This stage performs no lexical or domain inference.  It exists to turn a
    consumed pending interaction or approval into the exact command that was
    previously suspended.  Every other turn proceeds to the encoder or the
    Conversation Agent.
    """

    version = "state-bound-target-understanding-v1"

    async def __call__(
        self, observations, state, deterministic, registry, turn_context=None,
    ) -> TurnProposal | None:
        del observations, turn_context
        if (
            deterministic.kind is ResolutionKind.FILL_PENDING_INPUT
            and deterministic.resumed_work_items
        ):
            return TurnProposal(
                ProposalDisposition.RESOLVED,
                tuple(
                    self._resume_command(index, item)
                    for index, item in enumerate(
                        deterministic.resumed_work_items, start=1,
                    )
                ),
                "PENDING_INPUT_RESUMED",
            )

        if deterministic.kind in {
            ResolutionKind.APPROVAL_DECISION,
            ResolutionKind.APPROVAL_EXPIRED,
            ResolutionKind.RECONCILE_WORKFLOW,
        }:
            if not deterministic.approved:
                return TurnProposal(
                    ProposalDisposition.CLARIFY,
                    (),
                    (
                        "APPROVAL_EXPIRED"
                        if deterministic.kind is ResolutionKind.APPROVAL_EXPIRED
                        else "APPROVAL_DECLINED"
                    ),
                )
            action = registry.action(str(deterministic.action_ref))
            stream = next(
                item for item in state.workstreams
                if item.workstream_id == deterministic.workstream_id
            )
            return TurnProposal(
                ProposalDisposition.RESOLVED,
                (CommandProposal(
                    "continue-approved-workflow",
                    CommandKind.CONTINUE_ACTION,
                    action.owner_agent,
                    f"Execute explicitly approved action {action.action_id}",
                    tuple(
                        ArgumentValue.create(name, value)
                        for name, value in deterministic.arguments
                    ),
                    action.requirement_ids,
                    flow_ref=stream.flow_ref,
                    action_ref=deterministic.action_ref,
                    target_entity_ref=deterministic.target_entity_ref,
                    target_entity_version=deterministic.target_entity_version,
                    approval_binding=deterministic.signal_id,
                    approval_signal_version=deterministic.signal_version,
                    operation_key=deterministic.operation_key,
                    argument_bindings=deterministic.argument_bindings,
                ),),
                (
                    "RECONCILIATION_RESUME"
                    if deterministic.kind is ResolutionKind.RECONCILE_WORKFLOW
                    else "APPROVED_WORKFLOW_RESUME"
                ),
            )

        if deterministic.kind is ResolutionKind.CANCEL_WORKSTREAM:
            return TurnProposal(
                ProposalDisposition.CLARIFY, (), "WORKSTREAM_CANCELLED",
            )
        if deterministic.kind is ResolutionKind.CLARIFY_WORKSTREAM:
            return TurnProposal(
                ProposalDisposition.CLARIFY,
                (),
                "WORKSTREAM_TARGET_AMBIGUOUS",
                ("workstream_id",),
            )
        return None

    @staticmethod
    def _resume_command(index, item) -> CommandProposal:
        if item.control_mode in {ControlMode.WORKFLOW, ControlMode.ACTION}:
            raise ValueError(
                "business workflows use their approval/resume contract"
            )
        common = {
            "command_id": f"resume-input-{index}",
            "target_agent": item.owner_agent,
            "objective": item.objective,
            "arguments": item.arguments,
            "requirement_ids": item.requirement_ids,
            "argument_bindings": item.argument_bindings,
        }
        if item.control_mode is ControlMode.DIRECT:
            if len(item.allowed_tools) != 1:
                raise ValueError("resumed direct work must bind one tool")
            return CommandProposal(
                kind=CommandKind.DIRECT_TOOL,
                tool_id=item.allowed_tools[0],
                **common,
            )
        if item.skill_hint is not None:
            return CommandProposal(
                kind=CommandKind.RUN_SKILL,
                skill_id=item.skill_hint,
                **common,
            )
        return CommandProposal(
            kind=CommandKind.DELEGATE_TASK,
            candidate_skill_ids=item.allowed_skills,
            **common,
        )


class CascadedTargetUnderstanding:
    """Resolve state, then try the encoder, then invoke one global planner."""

    version = "cascaded-target-understanding-v3"

    def __init__(self, state_bound, planner, *, encoder=None) -> None:
        self._state_bound = state_bound
        self._planner = planner
        self._encoder = encoder

    async def __call__(
        self, observations, state, deterministic, registry, turn_context=None,
    ):
        resolved = await self._state_bound(
            observations, state, deterministic, registry, turn_context,
        )
        if resolved is not None:
            return resolved
        if self._encoder is not None:
            decision = await self._encoder(
                observations, state, registry, turn_context,
            )
            if decision.accepted:
                return decision.proposal
        return await self._planner.plan(
            observations, state, deterministic, registry, turn_context,
        )
