"""Final prompt budget owner at the real provider-call boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.model_policy import ModelProfile, ModelRole
from core.token_estimator import TokenEstimator


@dataclass(frozen=True)
class ProviderContextUsage:
    system_tokens: int
    message_tokens: int
    tool_schema_tokens: int
    protocol_tokens: int
    output_reserve_tokens: int
    max_context_tokens: int

    @property
    def estimated_input_tokens(self) -> int:
        return (
            self.system_tokens + self.message_tokens
            + self.tool_schema_tokens + self.protocol_tokens
        )

    @property
    def total_reserved_tokens(self) -> int:
        return self.estimated_input_tokens + self.output_reserve_tokens


class ProviderContextBudgetExceeded(RuntimeError):
    def __init__(self, role: ModelRole, usage: ProviderContextUsage):
        self.role = role
        self.usage = usage
        super().__init__(
            f"provider context budget exceeded for {role.value}: "
            f"required={usage.total_reserved_tokens}, "
            f"limit={usage.max_context_tokens}"
        )


class ProviderContextBudget:
    """Accounts for every prompt-bearing field without changing its meaning."""

    def __init__(self, estimator: TokenEstimator | None = None):
        self.estimator = estimator or TokenEstimator()

    def validate(
        self,
        profile: ModelProfile,
        role: ModelRole,
        request: Mapping[str, Any],
    ) -> ProviderContextUsage:
        messages = request.get("messages") or ()
        tools = request.get("tools") or ()
        usage = ProviderContextUsage(
            system_tokens=self.estimator.estimate(request.get("system")),
            message_tokens=self.estimator.estimate_messages(messages),
            tool_schema_tokens=(
                self.estimator.estimate(tools) + 4 * len(tools) if tools else 0
            ),
            protocol_tokens=8 + 2 * len(messages),
            output_reserve_tokens=max(0, int(request.get("max_tokens") or 0)),
            max_context_tokens=profile.max_context_tokens,
        )
        if usage.total_reserved_tokens > profile.max_context_tokens:
            raise ProviderContextBudgetExceeded(role, usage)
        return usage


DEFAULT_PROVIDER_CONTEXT_BUDGET = ProviderContextBudget()
