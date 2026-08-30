"""分层模型策略的默认值、校验和请求协议测试。"""

import pytest

from core.model_policy import ModelPolicy, ModelProfile, ModelRole, ReasoningEffort


def test_deepseek_defaults_tier_closed_tasks_and_quality_gates():
    policy = ModelPolicy.from_env({
        "MODEL_PROVIDER": "deepseek",
        "ANTHROPIC_API_KEY": "unused-here",
    })

    for role in (ModelRole.INTENT, ModelRole.WORKER, ModelRole.REACT,
                 ModelRole.MEMORY, ModelRole.REWRITE, ModelRole.RERANK):
        assert policy.profile(role).to_dict() == {
            "model": "deepseek-v4-flash", "reasoning": "none",
            "min_completion_tokens": 0,
        }
    for role in (ModelRole.SYNTHESIS, ModelRole.VERIFIER):
        assert policy.profile(role).to_dict() == {
            "model": "deepseek-v4-pro", "reasoning": "none",
            "min_completion_tokens": 0,
        }
    assert policy.profile(ModelRole.JUDGE).to_dict() == {
        "model": "deepseek-v4-pro", "reasoning": "none",
        "min_completion_tokens": 0,
    }
    assert policy.base_url == "https://api.deepseek.com/anthropic"


def test_deepseek_none_explicitly_disables_provider_default_thinking():
    request = ModelProfile(
        "deepseek-v4-flash", ReasoningEffort.NONE, "deepseek",
    ).request(max_tokens=64, temperature=0.2)

    assert request["model"] == "deepseek-v4-flash"
    assert request["temperature"] == 0.2
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


def test_deepseek_reasoning_sets_effort_and_removes_ignored_temperature():
    request = ModelProfile(
        "deepseek-v4-pro", ReasoningEffort.HIGH, "deepseek", 1024,
    ).request(max_tokens=64, temperature=0)

    assert "temperature" not in request
    assert request["max_tokens"] == 1024
    assert request["extra_body"] == {
        "thinking": {"type": "enabled"},
        "output_config": {"effort": "high"},
    }


@pytest.mark.parametrize("env", [
    {"MODEL_PROVIDER": "deepseek", "MODEL_WORKER": "unknown"},
    {"MODEL_PROVIDER": "deepseek", "MODEL_WORKER_REASONING": "medium"},
    {"MODEL_PROVIDER": "deepseek", "MODEL_JUDGE_REASONING": "high", "MODEL_JUDGE_MIN_COMPLETION_TOKENS": "64"},
    {"MODEL_PROVIDER": "other"},
])
def test_unsupported_policy_fails_at_startup(env):
    with pytest.raises(ValueError):
        ModelPolicy.from_env(env)
