"""Explicit text-only planning transport for historical evaluation comparisons."""
import json

from application.conversation_agent import ConversationProviderOutputError
from core.model_policy import ModelRole
from core.provider_context_budget import DEFAULT_PROVIDER_CONTEXT_BUDGET
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider


class LegacyTextPlanningProvider(AnthropicConversationPlanningProvider):
    version = "evaluation-legacy-text-planning-v1"

    async def _complete(self, payload, role, system):
        if role is not ModelRole.INTENT:
            raise ValueError('legacy evaluation provider supports planning only')
        system = system.replace(
            'Submit one complete plan through submit_turn_plan. Use the output tool rather than a text answer.',
            'Return one JSON object only.')
        request = self._model_profile.request(max_tokens=self._max_tokens, system=system,
            messages=[{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, sort_keys=True)}])
        DEFAULT_PROVIDER_CONTEXT_BUDGET.validate(self._model_profile, role, request)
        response = await self._client.messages.create(**request)
        text = ''.join(str(getattr(b, 'text', '')) for b in getattr(response, 'content', ())
                       if getattr(b, 'type', '') == 'text').strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConversationProviderOutputError('legacy planner returned invalid JSON') from exc
        if not isinstance(value, dict):
            raise ConversationProviderOutputError('legacy planner returned non-object JSON')
        return value
