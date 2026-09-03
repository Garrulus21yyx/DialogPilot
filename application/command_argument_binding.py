"""Bind explicit identifiers to already-selected Registry commands."""

from __future__ import annotations

import re
from dataclasses import replace

from application.route_policy_v2 import FlowActionRegistry
from application.turn_understanding import (
    CommandArgument,
    CommandKind,
    UnderstandingResult,
    UnderstandingStatus,
)


_ORDER_ID = re.compile(
    r"(?:订单(?:号|编号|ID)?|order(?:[_\s-]?id)?)\s*[:：#]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9_-]{2,127})",
    re.IGNORECASE,
)


class ExplicitIdentifierArgumentBinder:
    """Extract only field-labelled IDs; semantic routing remains upstream."""

    version = "explicit-identifier-binder-v1"

    def bind(
        self,
        understanding: UnderstandingResult,
        message: str,
        registry: FlowActionRegistry,
    ) -> UnderstandingResult:
        if understanding.status is not UnderstandingStatus.RESOLVED:
            return understanding
        commands = []
        for proposal in understanding.commands:
            if proposal.kind is CommandKind.RESPOND_DIRECT:
                commands.append(proposal)
                continue
            action = registry.action_for(proposal)
            required = action.required_arguments
            if not required:
                commands.append(proposal)
                continue
            if required != ("order_id",):
                raise ValueError("explicit identifier binder has unsupported arguments")
            existing = {item.name: item for item in proposal.arguments}
            if "order_id" not in existing:
                matches = tuple(dict.fromkeys(_ORDER_ID.findall(message)))
                if len(matches) != 1:
                    return UnderstandingResult(
                        UnderstandingStatus.CLARIFY,
                        reason_code="EXPLICIT_ORDER_ID_REQUIRED",
                    )
                existing["order_id"] = CommandArgument.create("order_id", matches[0])
            commands.append(
                replace(
                    proposal,
                    arguments=tuple(existing[name] for name in sorted(existing)),
                )
            )
        return UnderstandingResult(UnderstandingStatus.RESOLVED, tuple(commands))
