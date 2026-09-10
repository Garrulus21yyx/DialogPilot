"""Bounded recovery of a rejected model request, never an Agent/tool run."""
import logging
from application.context_budget import ModelContextBudgetExceeded
from core.provider_context_budget import ProviderContextBudgetExceeded

logger = logging.getLogger(__name__)


class ContextRecoveryExhausted(ModelContextBudgetExceeded):
    """The local estimate may fit even though the provider still rejects it."""
    def __init__(self, required, available, *, reason, attempts):
        super().__init__(required, available)
        self.reason = reason
        self.attempts = attempts
        self.args = (f"context recovery exhausted: {reason}; attempts={attempts}; "
                     f"estimated_input={required}; budget={available}",)


def is_context_overflow(error):
    """Classify input capacity errors, not generic 400/413 or output truncation."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (ModelContextBudgetExceeded, ProviderContextBudgetExceeded)):
            return True
        if getattr(error, "status_code", None) in {400, 413, 422}:
            body = getattr(error, "body", None)
            detail = body.get("error", body) if isinstance(body, dict) else {}
            code = detail.get("code", detail.get("type")) if isinstance(detail, dict) else None
            if code in {"context_length_exceeded", "prompt_too_long", "input_too_long"}:
                return True
            text = str(error).lower()
            if any(term in text for term in (
                "prompt is too long", "prompt too long", "maximum context length",
                "exceeds the context window", "input exceeds the context length",
            )):
                return True
        error = error.__cause__
    return False


async def recover_model_request(value, *, invoke, shrink, measure, available, max_recoveries=2):
    """Caller owns semantic projection. Each retry must demonstrably shrink it.

    `invoke` must contain only admission + one model call. `shrink` cannot run
    business tools; it may archive and summarize history. The original exception
    remains chained when there is no safe smaller request.
    """
    for attempt in range(max_recoveries + 1):
        try:
            return await invoke(value)
        except Exception as error:
            if not is_context_overflow(error):
                raise
            before = measure(value)
            if attempt == max_recoveries:
                raise ContextRecoveryExhausted(before, available,
                    reason="attempt_limit", attempts=attempt + 1) from error
            target = max(1, int(min(before, available) * .8))
            smaller = await shrink(value, target)
            after = measure(smaller)
            if after >= before:
                raise ContextRecoveryExhausted(before, target,
                    reason="no_input_reduction", attempts=attempt + 1) from error
            logger.info("Retrying rejected model step with smaller context", extra={
                "context_recovery": {"attempt": attempt + 1, "before_tokens": before,
                                     "after_tokens": after, "target_tokens": target}})
            value = smaller
