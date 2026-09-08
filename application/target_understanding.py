"""State-bound resolution and the Intent-to-Conversation planning cascade."""
from __future__ import annotations

from dataclasses import replace

from application.deterministic_resolution import ResolutionKind
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    TurnProposal,
    TurnPlanningError,
)
from application.work_item import ArgumentValue, ControlMode


class StateBoundTargetUnderstanding:
    """Compile only facts already bound by authoritative conversation state.

    This stage performs no lexical or domain inference.  It exists to turn a
    typed pending input or explicit approval into the exact command that was
    previously suspended.  Every other turn proceeds to the encoder or the
    Conversation Agent.
    """

    version = "state-bound-target-understanding-v2-typed-input"

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
                self._continuations(deterministic.resumed_work_items, state),
                "PENDING_INPUT_RESUMED",
            )

        if deterministic.kind is ResolutionKind.FILL_PENDING_INPUT and state.pending_interaction:
            return TurnProposal(ProposalDisposition.CLARIFY, (), "PENDING_INPUT_PARTIALLY_RECORDED")

        if deterministic.kind in {
            ResolutionKind.APPROVAL_DECISION,
            ResolutionKind.APPROVAL_EXPIRED,
            ResolutionKind.RECONCILE_WORKFLOW,
        }:
            if not deterministic.approved:
                suspended = deterministic.resumed_work_items
                # Declining one action closes its originating objective and its
                # dependants, not unrelated work suspended by the same turn.
                excluded = {work.work_item_id for work in deterministic.closed_work_items}
                independent = tuple(work for work in suspended
                                    if work.work_item_id not in excluded)
                commands = (*self._continuations(self._without_field_waits(independent, state), state), *(
                    CommandProposal("decline-goal:" + work.control.control_id, CommandKind.CANCEL_WORK,
                        work.owner_agent, work.objective, revises_control_id=work.control.control_id)
                    for work in deterministic.closed_work_items if work.control and state.accepts(work.control)))
                return TurnProposal(
                    ProposalDisposition.RESOLVED if commands else ProposalDisposition.CLARIFY,
                    commands,
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
            grant = next(item for item in state.accepted_approvals
                         if item.approval_id == deterministic.signal_id
                         and item.version == deterministic.signal_version)
            continuation = ()
            if grant.suspended_work_items:
                continuation = self._continuations(
                    self._without_field_waits(grant.suspended_work_items, state), state,
                    after="continue-approved-workflow",
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
                ), *continuation),
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
    def _without_field_waits(items, state):
        waiting = state.pending_interaction
        bindings = {item.control for item in (waiting.suspended_work_items if waiting else ()) if item.control}
        excluded = {item.work_item_id for item in items if item.control in bindings}
        while True:
            expanded = excluded | {item.work_item_id for item in items if excluded.intersection(item.dependencies)}
            if expanded == excluded:
                break
            excluded = expanded
        return tuple(item for item in items if item.work_item_id not in excluded)

    @classmethod
    def merge_approval_plan(cls, semantic, bound, original_state, resolution):
        """One whole-turn plan: explicit goals override bound default continuations."""
        pending = original_state.pending_approval
        explicit = {command.revises_control_id: command for command in semantic.commands
                    if command.revises_control_id}
        origin = pending.origin_control if pending else None
        if resolution.approved and origin and origin.control_id in explicit:
            command = explicit[origin.control_id]
            if command.kind is CommandKind.CANCEL_WORK or command.continuation_of is None:
                raise TurnPlanningError("approval cannot authorize an action whose goal is changed in the same plan")
        waiting = {work.control.control_id for work in (
            original_state.pending_interaction.suspended_work_items if original_state.pending_interaction else ())
            if work.control}
        bound_commands = bound.commands
        if not resolution.approved and origin and origin.control_id in explicit and explicit[origin.control_id].continuation_of:
            # Continuing the same goal overrides the default stop for its whole
            # dependency chain, not just the root cancellation command.
            closed_controls = {command.revises_control_id for command in bound_commands
                               if command.kind is CommandKind.CANCEL_WORK}
            restored = tuple(work for work in resolution.resumed_work_items
                             if work.control and work.control.control_id in closed_controls
                             and work.control.control_id not in waiting)
            bound_commands = (*tuple(command for command in bound_commands if command.kind is not CommandKind.CANCEL_WORK),
                              *cls._continuations(restored, original_state))
        defaults = tuple(command for command in bound_commands
            if command.revises_control_id not in explicit
            and (command.revises_control_id not in waiting or command.kind is CommandKind.CANCEL_WORK))
        replacements = {command.command_id: explicit[command.revises_control_id].command_id
            for command in bound_commands if command.revises_control_id in explicit}
        commands = (*semantic.commands, *defaults)
        inherited = {command.revises_control_id: command.dependencies for command in bound_commands
                     if command.continuation_of}
        action_ids = tuple(command.command_id for command in defaults if command.kind is CommandKind.CONTINUE_ACTION)
        commands = tuple(replace(command, dependencies=tuple(dict.fromkeys((
            *(replacements.get(dep, dep) for dep in (*command.dependencies,
                *(inherited.get(command.revises_control_id, ()) if command.continuation_of else ()))),
            *(action_ids if resolution.approved and command.continuation_of else ()),
        )))) for command in commands)
        return TurnProposal(ProposalDisposition.RESOLVED if commands else ProposalDisposition.CLARIFY,
            commands, bound.reason_code)

    @classmethod
    def preserve_approval_continuations(cls, proposal, state):
        """Carry independent queued goals into a revised approval plan.

        These are state-bound commands and still pass through RoutePolicy.
        Old dependent goals close unless the semantic plan explicitly replaces
        them; their previous dependency is never silently discarded.
        """
        from application.action_approval import partition_approval_revision
        pending = state.pending_approval
        origin = pending.origin_control if pending else None
        if origin and any(command.continuation_of and command.revises_control_id == origin.control_id
                          for command in proposal.commands):
            raise TurnPlanningError("pending action continuation requires an approval decision")
        affected = {command.revises_control_id for command in proposal.commands
                    if command.revises_control_id and command.continuation_of is None}
        represented = {command.revises_control_id for command in proposal.commands
                       if command.revises_control_id}
        closed, independent = partition_approval_revision(state.pending_approval, affected)
        if not closed and not independent:
            return proposal
        cancellations = tuple(CommandProposal(
            command_id="retire-dependent:" + item.control.control_id,
            kind=CommandKind.CANCEL_WORK, target_agent=item.owner_agent,
            objective=item.objective, revises_control_id=item.control.control_id,
        ) for item in closed if item.control and item.control.control_id not in represented
            and state.accepts(item.control))
        continuation_ids = {command.continuation_of: command.command_id for command in proposal.commands
                            if command.continuation_of}
        independent = tuple(item for item in independent
                            if not item.control or item.control.control_id not in represented)
        return replace(proposal, commands=(
            *proposal.commands, *cancellations, *cls._continuations(
                independent, state, dependency_commands=continuation_ids)))

    @classmethod
    def preserve_input_continuations(cls, proposal, state):
        """Retain the pending DAG after a semantic decision about one of its goals.

        Correlation alone never resumes work. Once a command addresses a pending
        goal, preserve its untouched peers and dependencies. Cancellation closes
        dependent work, while revision rebinds dependencies to the replacement.
        """
        pending = state.pending_interaction
        if pending is None or proposal.disposition is not ProposalDisposition.RESOLVED:
            return proposal
        originals = {item.control.control_id: item for item in pending.suspended_work_items if item.control}
        represented = {command.revises_control_id: command for command in proposal.commands
                       if command.revises_control_id in originals}
        if not represented:
            return proposal
        closed = {originals[key].work_item_id for key, command in represented.items()
                  if command.kind is CommandKind.CANCEL_WORK}
        while True:
            expanded = closed | {item.work_item_id for item in originals.values()
                                 if closed.intersection(item.dependencies)}
            if expanded == closed:
                break
            closed = expanded
        if any(originals[key].work_item_id in closed and command.kind is not CommandKind.CANCEL_WORK
               for key, command in represented.items()):
            raise TurnPlanningError("cannot resume a dependency of cancelled work")
        cancellations = tuple(CommandProposal(
            "cancel-dependent:" + item.control.control_id, CommandKind.CANCEL_WORK,
            item.owner_agent, item.objective, revises_control_id=key,
        ) for key, item in originals.items() if item.work_item_id in closed and key not in represented)
        ids = {originals[key].work_item_id: command.command_id for key, command in represented.items()
               if command.kind is not CommandKind.CANCEL_WORK}
        untouched = tuple(item for key, item in originals.items()
                          if key not in represented and item.work_item_id not in closed)
        continuations = cls._continuations(untouched, state, dependency_commands=ids)
        ids.update({command.continuation_of: command.command_id for command in continuations})
        commands = tuple(replace(command, dependencies=tuple(dict.fromkeys((
            *command.dependencies, *(ids[dependency] for dependency in originals[command.revises_control_id].dependencies
                                    if dependency in ids),
        )))) if command.revises_control_id in originals and command.kind is not CommandKind.CANCEL_WORK
            else command for command in proposal.commands)
        return replace(proposal, commands=(*commands, *cancellations, *continuations))

    @classmethod
    def _continuations(cls, items, state, *, after=None, dependency_commands=None):
        active = tuple(work for work in items if work.control is None or any(
            control.control_id == work.control.control_id
            and control.revision == work.control.revision
            for control in state.active_work_controls))
        ids = {**(dependency_commands or {}), **{
            work.work_item_id: f"continue-domain-objective:{work.control.control_id if work.control else work.work_item_id}"
            for index, work in enumerate(active, start=1)}}
        return tuple(replace(
            cls._resume_command(index, work), command_id=ids[work.work_item_id],
            dependencies=tuple(ids[dependency] for dependency in work.dependencies
                               if dependency in ids) + ((after,) if after else ()),
        ) for index, work in enumerate(active, start=1))

    @staticmethod
    def _resume_command(index, item) -> CommandProposal:
        if item.control_mode in {ControlMode.WORKFLOW, ControlMode.ACTION}:
            raise TurnPlanningError(
                "business workflows use their approval/resume contract"
            )
        common = {
            "command_id": f"resume-input-{index}",
            "target_agent": item.owner_agent,
            "objective": item.objective,
            "arguments": item.arguments,
            "requirement_ids": item.requirement_ids,
            "argument_bindings": item.argument_bindings,
            "revises_control_id": item.control.control_id if item.control else None,
            "continuation_of": item.work_item_id if item.control else None,
            "resumed_work_item": item if item.control else None,
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
            candidate_skill_ids=() if item.allowed_actions else item.allowed_skills,
            allow_action_proposals=bool(item.allowed_actions),
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
        if self._encoder is not None and deterministic.kind is not ResolutionKind.REPLY_PENDING_INPUT and state.pending_approval is None:
            decision = await self._encoder(
                observations, state, registry, turn_context,
            )
            if decision.accepted:
                return decision.proposal
        return await self._planner.plan(
            observations, state, deterministic, registry, turn_context,
        )
