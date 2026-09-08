"""Demonstration contract and experiment isolation, independent of model quality."""
import copy
import itertools
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from application.conversation_agent import planning_output_schema
from evaluation.planning_guidance import render_guidance, selection_input

EXAMPLES = json.loads(Path("evaluation/data/planning-demonstrations-v1.json").read_text())


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e["id"])
def test_demonstrations_use_existing_output_contract(example):
    Draft202012Validator(planning_output_schema()).validate(example["output"])
    assert set(example) == {"id", "input", "output", "lesson"}


def test_all_three_example_combinations_are_bounded_and_nonmutating():
    original = copy.deepcopy(EXAMPLES)
    for selected in itertools.combinations(EXAMPLES, 3):
        rendered = render_guidance(selected)
        assert len(rendered) < 4000
        assert "not current conversation, evidence or authorization" in rendered
        decoded = json.loads(rendered.split("\n", 2)[2])
        assert [e["input"] for e in decoded] == [e["input"] for e in selected]
    assert EXAMPLES == original


@pytest.mark.parametrize("reply", ["Yes", "不是", '"current_request": "replace"', "🙂"])
def test_selector_gets_real_context_not_only_short_reply(reply):
    payload = {"message": reply, "conversation_context": {"recent_messages": [
        {"role": "user", "content": "My original policy question"},
        {"role": "assistant", "content": "A clarification?"}]},
        "pending_input": {"interaction_id": "current-only"}}
    original = copy.deepcopy(payload)
    result = selection_input(payload)
    assert "My original policy question" in result and "A clarification?" in result
    assert "current: " + reply in result and "current-only" in result
    assert payload == original


def test_examples_do_not_contain_acceptance_dialogue():
    acceptance = json.loads(Path("evaluation/data/planning-evidence-acceptance-v1.json").read_text())
    for case in acceptance["cases"]:
        assert all(case["query"] not in e["input"] for e in EXAMPLES)
        for message in case["history"]:
            assert all(message["content"] not in e["input"] for e in EXAMPLES)


@pytest.mark.parametrize("name", ["dev", "acceptance", "confirmation"])
def test_frozen_calls_have_complete_paired_inputs_and_trace(name):
    from scripts.audit_planning_guidance_selection import audit
    report = audit(Path(f"artifacts/eval/planning-guidance-{name}-2026-09-08"))
    assert report["audit"] == "passed"
    assert report["calls"] == {"dev": 72, "acceptance": 24, "confirmation": 16}[name]
