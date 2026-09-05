"""Transport-only adapter from the locked conversation schema to ChatCommand."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from application.chat_contracts import ChatCommand, ChatHandler, ChatOutcome


class LockedTransportError(ValueError):
    pass


@dataclass(frozen=True)
class LockedTransportTurn:
    case_id: str
    turn_id: str
    status: str
    score_eligible: bool
    outcome: ChatOutcome


class LockedConversationTransportAdapter:
    """Run locked inputs without treating a stubbed route as benchmark evidence."""

    status = "TRANSPORT_SMOKE"
    score_eligible = False

    def __init__(self, application: ChatHandler, dataset_root: str | Path) -> None:
        self._application = application
        self._dataset_root = Path(dataset_root)

    async def run(self, case_id: str) -> tuple[LockedTransportTurn, ...]:
        case = self._load(case_id)
        initial = dict(case["initial_state"])
        outputs = []
        for turn in case["turns"]:
            if turn.get("attachment_refs"):
                raise LockedTransportError(
                    "transport smoke currently supports L0 turns only"
                )
            turn_id = str(turn["turn_id"])
            outcome = await self._application.handle(ChatCommand(
                message=str(turn["user_message"]),
                tenant_id=str(initial["tenant_id"]),
                user_id=str(initial["user_id"]),
                conv_id=str(initial["conversation_id"]),
                request_id=_request_id(case_id, turn_id),
            ))
            outputs.append(LockedTransportTurn(
                case_id,
                turn_id,
                self.status,
                self.score_eligible,
                outcome,
            ))
        return tuple(outputs)

    def _load(self, case_id: str) -> dict[str, Any]:
        path = self._dataset_root / "cases.jsonl"
        cases = (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        try:
            case = next(item for item in cases if item.get("case_id") == case_id)
        except StopIteration as exc:
            raise LockedTransportError(f"unknown locked case: {case_id}") from exc
        if (
            case.get("schema_version") != "dialogpilot-synthetic-contract-case-v1"
            or case.get("status") != "SYNTHETIC_CONTRACT_LOCKED"
        ):
            raise LockedTransportError("case is not a locked conversation contract")
        return case


def _request_id(case_id: str, turn_id: str) -> str:
    digest = hashlib.sha256(f"{case_id}:{turn_id}".encode("utf-8")).hexdigest()
    return f"transport-smoke-{digest[:24]}"
