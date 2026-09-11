"""Structured calls through the installed LangChain model integration."""
from jsonschema import Draft202012Validator, ValidationError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables.config import ensure_config, merge_configs
from core.framework_models import invoke_model
from langchain_core.runnables import RunnableLambda


class ModelOutputError(ValueError):
    """A provider result cannot be consumed as the requested stage result."""


class StructuredOutputError(ModelOutputError):
    """The model returned an invalid stage result, not an invalid caller input."""


def structured_tool(name, schema):
    # The integration requires an object root, including for union result schemas.
    wire_schema = {"type": "object", "properties": {"result": schema},
                   "required": ["result"], "additionalProperties": False}
    return {"name": name, "description": "Return the complete stage result.", "input_schema": wire_schema}


async def structured_call(model, *, name, schema, system, messages, callbacks=(), metadata=None,
                          protocol_attempts=1):
    if not messages or any(not isinstance(message, (HumanMessage, AIMessage, ToolMessage)) for message in messages):
        raise ValueError("structured_input_requires_conversation_messages")
    if protocol_attempts not in (1, 2):
        raise ValueError("structured protocol attempts must be one or two")
    # SDK owns tool binding, provider message normalization and JSON parsing.
    tool = structured_tool(name, schema)
    # Keep the enclosing SDK callback manager and its parent run identity.
    # Replacing it with a handler list starts a separate trace inside graph hooks.
    config = merge_configs(ensure_config(), {
        "callbacks": list(callbacks), "run_name": name, "metadata": metadata or {},
    })
    bound = model.with_structured_output(tool, include_raw=True)
    async def attempt(inputs):
        output = await invoke_model(bound.ainvoke(inputs, config=config), stage=name)
        return _stage_result(output, tool)
    # SDK owns finite retry of this pure model stage. Business tools, the
    # candidate and its evidence are outside the retry boundary.
    return await RunnableLambda(attempt).with_retry(
        retry_if_exception_type=(StructuredOutputError,),
        stop_after_attempt=protocol_attempts, wait_exponential_jitter=False,
    ).ainvoke([SystemMessage(system), *messages], config=config)


def _stage_result(output, tool):
    raw = output["raw"]
    if raw.response_metadata.get("stop_reason") in {"max_tokens", "refusal"}:
        raise ModelOutputError("structured_output_incomplete")
    if output["parsing_error"] is not None:
        raise StructuredOutputError("structured_output_parse_failed") from output["parsing_error"]
    if len(raw.tool_calls) != 1:
        raise StructuredOutputError("structured_output_requires_one_result")
    try:
        Draft202012Validator(tool["input_schema"]).validate(output["parsed"])
    except ValidationError as exc:
        raise StructuredOutputError("structured_output_schema_invalid") from exc
    return output["parsed"]["result"]
