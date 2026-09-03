"""Structured LLM understanding that proposes only Registry commands."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from application.route_policy_v2 import FlowActionRegistry, RoutePolicyError
from application.selective_command_producer import EncoderCommandCandidate
from application.structured_command_prompt import (
    COMMAND_ROUTER_PROMPT_VERSION,
    render_structured_command_prompt,
)
from application.turn_state import ActiveFlowRef, FlowDefinitionRef, TurnStateSnapshot
from application.turn_understanding import (
    ClarificationDecision,
    ClarificationReason,
    CommandArgument,
    CommandKind,
    CommandProposal,
    UnderstandingError,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
)


class CommandCompletionFailure(RuntimeError):
    """The completion transport did not produce a response."""


class CommandCompletionPort(Protocol):
    provider_version: str

    async def complete(self, *, system: str, input_json: str) -> str: ...


class _InvalidCommandOutput(ValueError):
    pass


class StructuredLLMCommandProducer:
    """Translate strict provider JSON into non-authoritative proposals."""

    def __init__(
        self,
        completion: CommandCompletionPort,
        *,
        producer_version: str = COMMAND_ROUTER_PROMPT_VERSION,
    ) -> None:
        self._completion = completion
        self._producer_version = producer_version

    async def produce(
        self,
        message: str,
        message_fingerprint: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        candidates: tuple[EncoderCommandCandidate, ...] = (),
        *,
        history: tuple[Mapping[str, str], ...] = (),
        bundle: Any = None,
    ) -> UnderstandingResult:
        del bundle
        prompt = render_structured_command_prompt(
            message, state, registry, candidates, history
        )
        try:
            raw = await self._completion.complete(
                system=prompt.system,
                input_json=prompt.input_json,
            )
        except CommandCompletionFailure:
            return UnderstandingResult(
                UnderstandingStatus.PROVIDER_FAILURE,
                reason_code="COMMAND_COMPLETION_FAILED",
            )
        try:
            return self._parse(raw, message_fingerprint, state, registry)
        except (
            _InvalidCommandOutput,
            UnderstandingError,
            RoutePolicyError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return UnderstandingResult(
                UnderstandingStatus.INVALID_PROVIDER_OUTPUT,
                reason_code="COMMAND_OUTPUT_INVALID",
            )

    def _parse(
        self,
        raw: str,
        message_fingerprint: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
    ) -> UnderstandingResult:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {
            "status", "commands", "clarification"
        }:
            raise _InvalidCommandOutput("command output root is invalid")
        status = UnderstandingStatus(value["status"])
        if status not in {
            UnderstandingStatus.RESOLVED,
            UnderstandingStatus.CLARIFY,
            UnderstandingStatus.NO_SUPPORTED_FLOW,
        }:
            raise _InvalidCommandOutput("provider cannot emit this status")
        raw_commands = value["commands"]
        raw_clarification = value["clarification"]
        if not isinstance(raw_commands, list):
            raise _InvalidCommandOutput("commands must be a list")
        if status is not UnderstandingStatus.RESOLVED:
            if raw_commands:
                raise _InvalidCommandOutput("terminal status cannot carry commands")
            if status is UnderstandingStatus.CLARIFY:
                clarification = _clarification(raw_clarification, registry)
                return UnderstandingResult(
                    status,
                    reason_code=f"LLM_{clarification.reason.value}",
                    clarification=clarification,
                )
            if raw_clarification is not None:
                raise _InvalidCommandOutput(
                    "non-clarify terminal cannot carry clarification"
                )
            return UnderstandingResult(status, reason_code="LLM_NO_SUPPORTED_FLOW")

        if raw_clarification is not None:
            raise _InvalidCommandOutput("RESOLVED cannot carry clarification")

        commands = tuple(
            self._command(item, message_fingerprint, state, registry)
            for item in raw_commands
        )
        return UnderstandingResult(UnderstandingStatus.RESOLVED, commands)

    def _command(
        self,
        value: object,
        message_fingerprint: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
    ) -> CommandProposal:
        fields = {
            "kind",
            "source_flow_instance_id",
            "target_flow_id",
            "target_flow_version",
            "arguments",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise _InvalidCommandOutput("command shape is invalid")
        kind = CommandKind(value["kind"])
        source_id = _optional_string(value["source_flow_instance_id"])
        target_id = _optional_string(value["target_flow_id"])
        target_version = _optional_string(value["target_flow_version"])
        raw_arguments = value["arguments"]
        if not isinstance(raw_arguments, dict) or any(
            not isinstance(name, str) or not name.strip()
            for name in raw_arguments
        ):
            raise _InvalidCommandOutput("command arguments must be an object")
        if (target_id is None) != (target_version is None):
            raise _InvalidCommandOutput("target flow identity is incomplete")
        source = _active_flow(source_id, state)
        target = (
            FlowDefinitionRef(target_id, target_version)
            if target_id is not None and target_version is not None
            else None
        )
        proposal = CommandProposal(
            kind=kind,
            source=UnderstandingSource.LLM,
            message_fingerprint=message_fingerprint,
            producer_version=(
                f"{self._producer_version}+{self._completion.provider_version}"
            ),
            evidence_refs=(f"message:{message_fingerprint}",),
            source_flow=source,
            target_flow=target,
            arguments=tuple(
                CommandArgument.create(name, raw_arguments[name])
                for name in sorted(raw_arguments)
            ),
        )
        _require_registry_command(proposal, registry)
        return proposal


def _active_flow(
    instance_id: str | None,
    state: TurnStateSnapshot,
) -> ActiveFlowRef | None:
    if instance_id is None:
        return None
    matching = tuple(
        item for item in state.active_flows if item.instance_id == instance_id
    )
    if len(matching) != 1:
        raise _InvalidCommandOutput("source flow instance is unavailable")
    return matching[0]


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _InvalidCommandOutput("flow identity must be a non-blank string or null")
    return value


def _require_registry_command(
    proposal: CommandProposal,
    registry: FlowActionRegistry,
) -> None:
    flows = tuple(
        flow
        for flow in (
            proposal.source_flow.definition if proposal.source_flow else None,
            proposal.target_flow,
        )
        if flow is not None
    )
    if flows:
        for flow in flows:
            if proposal.kind not in registry.flow(flow).allowed_commands:
                raise _InvalidCommandOutput("command is not allowed for this flow")
    registry.action_for(proposal)


def _clarification(
    value: object,
    registry: FlowActionRegistry,
) -> ClarificationDecision:
    fields = {"reason", "missing_dimensions", "candidate_flow_ids"}
    if not isinstance(value, dict) or set(value) != fields:
        raise _InvalidCommandOutput("clarification shape is invalid")
    missing = value["missing_dimensions"]
    candidates = value["candidate_flow_ids"]
    if not isinstance(missing, list) or not all(
        isinstance(item, str) for item in missing
    ):
        raise _InvalidCommandOutput("missing dimensions must be strings")
    if not isinstance(candidates, list) or not all(
        isinstance(item, str) for item in candidates
    ):
        raise _InvalidCommandOutput("candidate flows must be strings")
    registered = {item.ref.flow_id for item in registry.flows}
    if not set(candidates).issubset(registered):
        raise _InvalidCommandOutput("clarification references unsupported flow")
    return ClarificationDecision(
        ClarificationReason(value["reason"]),
        tuple(missing),
        tuple(candidates),
    )
