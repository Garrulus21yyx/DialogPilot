"""Anthropic Messages transport for structured command completion."""

from __future__ import annotations

from typing import Any

from application.structured_command_producer import CommandCompletionFailure
from core.llm_metrics import create_message
from core.llm_utils import extract_text_content
from core.model_policy import ModelProfile, ModelRole


class AnthropicCommandCompletion:
    """Adapt the existing observed Messages seam to the small completion port."""

    def __init__(
        self,
        client: Any,
        model_profile: ModelProfile,
        *,
        max_tokens: int = 768,
    ) -> None:
        if max_tokens < 1:
            raise ValueError("command completion max_tokens must be positive")
        self._client = client
        self._model_profile = model_profile
        self._max_tokens = max_tokens
        self.provider_version = (
            f"{model_profile.provider}:{model_profile.model}:messages-v1"
        )

    async def complete(self, *, system: str, input_json: str) -> str:
        try:
            response = await create_message(
                self._client,
                self._model_profile,
                ModelRole.INTENT,
                system=system,
                messages=[{"role": "user", "content": input_json}],
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
        except Exception as exc:
            raise CommandCompletionFailure(
                "structured command completion failed"
            ) from exc
        content = getattr(response, "content", None)
        return extract_text_content(content) if content is not None else ""
