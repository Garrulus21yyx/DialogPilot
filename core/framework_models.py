"""One model factory for all Target LangChain consumers."""
from langchain_anthropic import ChatAnthropic
from anthropic import APIConnectionError
import httpx
import os
from functools import lru_cache
from threading import BoundedSemaphore
from core.capacity_metrics import decisions


class ModelCapacityExceeded(RuntimeError):
    """No model request was sent. Retry at the run boundary, not in a loop."""


@lru_cache(maxsize=1)
def _model_slots():
    capacity = int(os.getenv("MODEL_MAX_CONCURRENT", "8"))
    if capacity < 1:
        raise ValueError("MODEL_MAX_CONCURRENT must be positive")
    return BoundedSemaphore(capacity)


class ModelInvocationError(RuntimeError):
    """Safe stage diagnosis; original exception remains in the trace cause."""
    def __init__(self, stage, cause):
        self.stage = stage
        self.error_type = type(cause).__name__
        self.retryable = retryable_model_error(cause)
        super().__init__(f"{stage}:{self.error_type}")


def retryable_model_error(error):
    if isinstance(error, ModelCapacityExceeded):
        return True
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status in {408, 409, 429} or status >= 500
    cause = error
    while cause is not None:
        if isinstance(cause, (TimeoutError, ConnectionError, httpx.TransportError, APIConnectionError)):
            return True
        cause = cause.__cause__
    return False


async def invoke_model(awaitable, *, stage):
    slots = _model_slots()
    if not slots.acquire(blocking=False):
        decisions.labels("target_model", "rejected").inc()
        # All callers pass an unstarted SDK coroutine. Close it without sending
        # the request; rejected calls must not leak coroutine/task resources.
        awaitable.close()
        cause = ModelCapacityExceeded("model concurrency exhausted")
        raise ModelInvocationError(stage, cause) from cause
    try:
        decisions.labels("target_model", "started").inc()
        return await awaitable
    except Exception as exc:
        raise ModelInvocationError(stage, exc) from exc
    finally:
        slots.release()


def framework_model(profile, provider_config, *, max_tokens=1024):
    _model_slots()  # Validate capacity at composition, not the first request.
    timeout = float(os.getenv("MODEL_REQUEST_TIMEOUT_SECONDS", "120"))
    if timeout <= 0:
        raise ValueError("MODEL_REQUEST_TIMEOUT_SECONDS must be positive")
    request = profile.request(max_tokens=max_tokens, temperature=0)
    extras = dict(request.get("extra_body", {}))
    thinking = extras.pop("thinking", None)
    return ChatAnthropic(
        model_name=request["model"], api_key=provider_config["api_key"],
        base_url=provider_config.get("base_url"), max_tokens=request["max_tokens"],
        thinking=thinking, max_retries=2,
        default_request_timeout=timeout,
        model_kwargs={"extra_body": extras} if extras else {},
    )


def conversation_models(policy, provider_config, *, max_tokens=800):
    from core.model_policy import ModelRole
    return {role: framework_model(policy.profile(role), provider_config, max_tokens=max_tokens)
            for role in (ModelRole.INTENT, ModelRole.SYNTHESIS)}
