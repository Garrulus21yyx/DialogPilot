import pytest
from langchain_core.messages import HumanMessage
from core.model_policy import ModelProfile, ReasoningEffort
from core.framework_models import framework_model


@pytest.mark.parametrize("effort", list(ReasoningEffort))
@pytest.mark.parametrize("floor", [3072, 4096])
def test_framework_request_preserves_role_policy(effort, floor):
    profile = ModelProfile("deepseek-v4-flash", reasoning=effort, provider="deepseek",
                           min_completion_tokens=floor)
    model = framework_model(profile, {"api_key": "test-key"})
    assert model.max_retries == 2  # Native SDK transport retries, not another Agent loop.
    payload = model._get_request_payload([HumanMessage(content="A customer objective.")])
    expected = profile.request(max_tokens=1024, temperature=0)
    for key in ("model", "max_tokens"):
        assert payload[key] == expected[key]
    assert {"thinking": payload["thinking"], **payload.get("extra_body", {})} == expected["extra_body"]
    assert payload["max_tokens"] == floor
