"""Protocol bridge for τ³ entry probes; never exposes task answers to the app.

An entry probe is deliberately not an official benchmark score. A full run also
requires an explicit business-tool/policy binding and the official orchestrator.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from application.chat_contracts import ChatCommand, Completed
from evaluation.chat_application_runner import ChatApplicationRunner


class Tau3ChatBridge:
    """Keep benchmark user text and DialogPilot invocation identities separate."""

    def __init__(self, runner: ChatApplicationRunner, *, tenant_id: str,
                 conversation_id: str, user_id: str) -> None:
        self.runner = runner
        self.tenant_id = tenant_id
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.turn = 0

    async def receive(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("benchmark user text must be nonempty")
        self.turn += 1
        result = await self.runner.run(ChatCommand(
            message=text, tenant_id=self.tenant_id, user_id=self.user_id,
            conv_id=self.conversation_id, request_id=f"turn-{self.turn}",
            authorization_fingerprint="isolated-tau3-probe",
        ))
        outcome = result.outcome
        return {
            "turn": self.turn,
            "user_message": text,
            "outcome_type": type(outcome).__name__,
            "outcome": asdict(outcome),
            "response": outcome.response.get("response", "") if isinstance(outcome, Completed) else None,
            "latency_ms": result.latency_ms,
            "owner_state": dict(result.owner_state),
        }


def capability_overlap(registry, official_tools) -> dict[str, Any]:
    """Report vocabulary overlap, not an assertion of semantic compatibility."""
    project = {tool.tool_id for tool in registry.tools}
    official = {tool.name for tool in official_tools}
    return {
        "project_tools": sorted(project),
        "official_tools": sorted(official),
        "exact_name_overlap": sorted(project & official),
        "semantic_bindings_verified": False,
        "official_reward_eligible": False,
    }
