"""SDK-backed scripted model: no network and no alternative production parser."""
from langchain_core.messages import AIMessage
from tests.test_target_framework_agent import ScriptedToolModel
from core.model_policy import ModelRole


class StructuredStub(ScriptedToolModel):
    def bind_tools(self, tools, **kwargs):
        self.bound_tool_names = [tool.get("name", tool.get("function", {}).get("name")) for tool in tools]
        return self


def models(value=None, *, name="submit_turn_plan", text=None, stop="tool_use", count=1):
    message = AIMessage(content=text or "", response_metadata={"stop_reason": stop},
        tool_calls=[] if text is not None else [
            {"name": name, "args": {"result": value}, "id": f"result-{i}", "type": "tool_call"}
            for i in range(count)])
    return {role: StructuredStub(responses=[message]) for role in (ModelRole.INTENT, ModelRole.SYNTHESIS)}


def action_models(*calls, text="", stop="tool_use", invalid=()):
    message = AIMessage(content=text, response_metadata={"stop_reason": stop},
        tool_calls=[{"name": name, "args": args, "id": f"call-{i}"}
                    for i, (name, args) in enumerate(calls)], invalid_tool_calls=list(invalid))
    return {role: StructuredStub(responses=[message]) for role in (ModelRole.INTENT, ModelRole.SYNTHESIS)}
