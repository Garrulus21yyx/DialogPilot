"""Structured calls through the installed LangChain model integration."""
from jsonschema import Draft202012Validator, ValidationError
from langchain_core.messages import HumanMessage, SystemMessage
from core.framework_models import invoke_model


def structured_tool(name, schema):
    # The integration requires an object root, including for union result schemas.
    wire_schema = {"type": "object", "properties": {"result": schema},
                   "required": ["result"], "additionalProperties": False}
    return {"name": name, "description": "Return the complete stage result.", "input_schema": wire_schema}


async def structured_call(model, *, name, schema, system, content, callbacks=(), metadata=None):
    # SDK owns tool binding, provider message normalization and JSON parsing.
    tool = structured_tool(name, schema)
    output = await invoke_model(model.with_structured_output(
        tool,
        include_raw=True,
    ).ainvoke([SystemMessage(system), HumanMessage(content)],
              config={"callbacks": list(callbacks), "run_name": name, "metadata": metadata or {}}), stage=name)
    if output["parsing_error"] is not None:
        raise ValueError("structured_output_parse_failed") from output["parsing_error"]
    raw = output["raw"]
    if raw.response_metadata.get("stop_reason") in {"max_tokens", "refusal"}:
        raise ValueError("structured_output_incomplete")
    if len(raw.tool_calls) != 1:
        raise ValueError("structured_output_requires_one_result")
    try:
        Draft202012Validator(tool["input_schema"]).validate(output["parsed"])
    except ValidationError as exc:
        raise ValueError("structured_output_schema_invalid") from exc
    return output["parsed"]["result"]
