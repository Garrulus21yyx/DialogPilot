"""One model factory for all Target LangChain consumers."""
from langchain_anthropic import ChatAnthropic
from anthropic import APIConnectionError
import httpx


class ModelInvocationError(RuntimeError):
    """Safe stage diagnosis; original exception remains in the trace cause."""
    def __init__(self, stage, cause):
        self.stage = stage
        self.error_type = type(cause).__name__
        self.retryable = retryable_model_error(cause)
        super().__init__(f"{stage}:{self.error_type}")


def retryable_model_error(error):
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
    try:
        return await awaitable
    except Exception as exc:
        raise ModelInvocationError(stage, exc) from exc


def framework_model(profile, provider_config, *, max_tokens=1024):
    request = profile.request(max_tokens=max_tokens, temperature=0)
    extras = dict(request.get("extra_body", {}))
    thinking = extras.pop("thinking", None)
    return ChatAnthropic(
        model_name=request["model"], api_key=provider_config["api_key"],
        base_url=provider_config.get("base_url"), max_tokens=request["max_tokens"],
        thinking=thinking, max_retries=2,
        model_kwargs={"extra_body": extras} if extras else {},
    )


def conversation_models(policy, provider_config, *, max_tokens=800):
    from core.model_policy import ModelRole
    return {role: framework_model(policy.profile(role), provider_config, max_tokens=max_tokens)
            for role in (ModelRole.INTENT, ModelRole.SYNTHESIS)}
