"""M4-T03P final provider-call budget invariant proofs."""
import json
from pathlib import Path

import pytest

from core.model_policy import ModelProfile, ModelRole
from core.provider_context_budget import (
    ProviderContextBudget,
    ProviderContextBudgetExceeded,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("tool_count", [0, 1, 10, 100])
@pytest.mark.parametrize("result_chars", [0, 100, 10_000])
def test_arbitrary_tool_schema_and_result_sizes_are_admitted_or_typed_rejected(
    tool_count, result_chars,
):
    profile = ModelProfile("test-model", max_context_tokens=2048)
    tools = [
        {
            "name": f"tool_{index}",
            "description": "bounded schema",
            "input_schema": {"type": "object", "properties": {}},
        }
        for index in range(tool_count)
    ]
    request = {
        "max_tokens": 256,
        "system": "mandatory security and task contract",
        "tools": tools,
        "messages": [{"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "call-1",
            "content": "x" * result_chars,
        }]}],
    }
    try:
        usage = ProviderContextBudget().validate(profile, ModelRole.REACT, request)
    except ProviderContextBudgetExceeded as exc:
        assert exc.usage.total_reserved_tokens > 2048
    else:
        assert usage.total_reserved_tokens <= 2048
        assert usage.tool_schema_tokens >= tool_count * 4


def test_frozen_provider_budget_contract_lists_every_prompt_component():
    contract = json.loads((
        ROOT / "governance/concurrency/m4-t03-provider-budget-v1.json"
    ).read_text("utf-8"))
    assert contract["owner"] == "create_message provider boundary"
    assert set(contract["accounted_components"]) == {
        "system", "messages_and_history", "tool_schemas",
        "protocol_overhead", "output_reserve",
    }
    assert contract["oversize_outcome"] == "ProviderContextBudgetExceeded"
    assert contract["provider_side_effect_started_on_rejection"] is False
