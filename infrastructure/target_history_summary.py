"""History-only summaries with SDK message-group boundaries and bounded work."""
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately, get_buffer_string

from application.context_budget import ModelContextBudgetExceeded
from core.framework_models import invoke_model
from infrastructure.target_model_recovery import is_context_overflow


async def summarize_history(model, messages, *, prompt, available_tokens, max_calls=8):
    calls = 0
    messages = list(messages)
    cuts = sorted({0, len(messages), *(SummarizationMiddleware._find_safe_cutoff_point(messages, i)
                                    for i in range(1, len(messages)))})
    # Fail before spending summary calls when even one indivisible original
    # group cannot fit. Large tool bodies belong to archive projection upstream.
    for start, end in zip(cuts, cuts[1:]):
        required = count_tokens_approximately([HumanMessage(
            prompt.format(messages=get_buffer_string(messages[start:end])))])
        if required > available_tokens:
            raise ModelContextBudgetExceeded(required, available_tokens)

    async def summarize(group):
        nonlocal calls
        text = prompt.format(messages=get_buffer_string(group))
        required = count_tokens_approximately([HumanMessage(text)])
        error = None
        if required <= available_tokens:
            if calls >= max_calls:
                raise ModelContextBudgetExceeded(required, available_tokens)
            calls += 1
            try:
                response = await invoke_model(model.ainvoke(text, config={
                    "run_name": "context_summary", "metadata": {"lc_source": "summarization"}
                }), stage="context_summary")
                if not response.text.strip() or response.response_metadata.get("stop_reason") in {"max_tokens", "refusal"}:
                    raise ValueError("summary_incomplete")
                return response.text.strip()
            except Exception as exc:
                if not is_context_overflow(exc):
                    raise
                error = exc
        # Use the installed SDK's pairing semantics, not a second protocol parser.
        boundaries = {SummarizationMiddleware._find_safe_cutoff_point(group, i)
                      for i in range(1, len(group))}
        boundaries.discard(0)
        if not boundaries:
            raise ModelContextBudgetExceeded(required, available_tokens) from error
        cut = min(boundaries, key=lambda i: abs(i - len(group) / 2))
        left = await summarize(group[:cut])
        right = await summarize(group[cut:])
        merged = [HumanMessage(content=left), HumanMessage(content=right)]
        merged_text = prompt.format(messages=get_buffer_string(merged))
        if count_tokens_approximately([HumanMessage(merged_text)]) >= required:
            raise ModelContextBudgetExceeded(required, available_tokens) from error
        return await summarize(merged)

    return await summarize(messages)
