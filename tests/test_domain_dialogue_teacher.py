"""Offline author/reviewer wiring; no network requests."""
import asyncio
import json
from types import SimpleNamespace

from core.model_policy import ModelRole
from evaluation import domain_dialogue_teacher as teacher


def test_author_uses_worker_review_uses_judge_and_preserves_existing(tmp_path, monkeypatch):
    profiles = {ModelRole.WORKER: SimpleNamespace(model="flash"),
                ModelRole.JUDGE: SimpleNamespace(model="pro")}
    policy = SimpleNamespace(profile=profiles.__getitem__, base_url="https://example.invalid")
    monkeypatch.setattr(teacher, "dotenv_values", lambda _: {"ANTHROPIC_API_KEY": "test-only"})
    monkeypatch.setattr(teacher.ModelPolicy, "from_env", lambda _: policy)
    monkeypatch.setattr(teacher, "framework_model", lambda profile, provider, **kwargs: profile.model)
    batch = teacher.AuthoredBatch(families=[teacher.DialogueFamily(
        scenario=f"Scenario {i}", messages=[teacher.Message(role="user", content=f"Order {i}?"),
                                          teacher.Message(role="assistant", content="Which question?")],
        variants=[teacher.Variant(text="Where is it?", label="order_logistics"),
                  teacher.Variant(text="Forget it.", label="__DEFER__"),
                  teacher.Variant(text="Get me a person.", label="human_service")]) for i in range(8)])
    calls = []

    async def invoke(model, schema, prompt, name):
        calls.append((model, schema, name))
        if schema is teacher.AuthoredBatch:
            return batch, {"response": {"usage_metadata": {"total_tokens": 10}}}
        cases = json.loads(prompt.split("\n", 1)[1])
        assert all(set(case) == {"case_id", "messages", "text"} for case in cases)
        return teacher.ReviewedBatch(verdicts=[teacher.Verdict(
            case_id=c["case_id"], label=batch.families[int(c["case_id"].split(":")[-2])]
            .variants[int(c["case_id"].split(":")[-1])].label,
            clear_and_natural=True, reason="reviewed") for c in cases]), {"response": {}}

    monkeypatch.setattr(teacher, "structured_call", invoke)
    asyncio.run(teacher.generate(tmp_path, 0, 1, ["en"]))
    assert [(model, name) for model, _, name in calls] == [
        ("flash", "domain_data_author"), ("pro", "domain_data_blind_review")]
    path = tmp_path / "en/batch-00.json"
    original = path.read_bytes()
    record = json.loads(original)
    assert record["models"] == {"author": "flash", "review": "pro"}
    assert record["status"] == "reviewed" and len(record["rows"]) == 24
    asyncio.run(teacher.generate(tmp_path, 0, 1, ["en"]))
    assert len(calls) == 2 and path.read_bytes() == original
