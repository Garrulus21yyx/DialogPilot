import pytest
from langchain_core.messages import HumanMessage
from core.model_policy import ModelProfile, ReasoningEffort
from infrastructure.target_runtime_composition import _framework_model


@pytest.mark.parametrize("effort", list(ReasoningEffort))
def test_framework_request_preserves_role_policy(effort):
    profile = ModelProfile("deepseek-v4-flash", reasoning=effort, provider="deepseek",
                           min_completion_tokens=3072 if effort is not ReasoningEffort.NONE else 0)
    model = _framework_model(profile, {"api_key": "test-key"})
    payload = model._get_request_payload([HumanMessage(content="A customer objective.")])
    expected = profile.request(max_tokens=1024, temperature=0)
    for key in ("model", "max_tokens", "extra_body"):
        assert payload[key] == expected[key]
