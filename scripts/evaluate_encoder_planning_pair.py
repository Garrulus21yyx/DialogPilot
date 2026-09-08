"""Small stratified real-planner ON/OFF probe, not a population latency estimate."""
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

from dotenv import dotenv_values
from application.conversation_agent import ConversationAgent
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from application.turn_planning import RoutePolicy
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelProfile, ModelRole, ReasoningEffort
from evaluation.encoder_fastpath_evaluation import prepare
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from infrastructure.target_runtime_composition import _target_encoder


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/eval/encoder-language-fastpath-2026-09-08/live-pair"


async def main():
    if OUT.exists():
        raise ValueError("live output already exists")
    source = ROOT / "data/eval/encoder-language-independent-2026-09-08.jsonl"
    cases = {row["case_id"]: row for row in map(json.loads, source.read_text().splitlines())}
    audit = json.loads((OUT.parent / "independent.json").read_text())
    selected = []
    for language in ("zh", "en"):
        group = [row for row in audit["details"] if row["language"] == language]
        positive = [row for row in group if row["accepted"]][:3]
        deferred = [row for row in group if not row["accepted"]][:5-len(positive)]
        selected.extend(positive + deferred)
    OUT.mkdir()
    manifest = {"case_ids": [row["case_id"] for row in selected], "max_model_calls": 20,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "selection": "first up to3 accepted + deferred to5 per language; stratified diagnostic, not prevalence",
        "model": "deepseek-v4-flash", "reasoning": "none", "retries": 0,
        "scope": "planning only; no business tools, no answer generation"}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    values = {**{k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}, **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = ModelProfile(model="deepseek-v4-flash", provider="deepseek", reasoning=ReasoningEffort.NONE)
    model = framework_model(profile, {"api_key": values["ANTHROPIC_API_KEY"], "base_url": policy.base_url}, max_tokens=2048)
    capture = FrameworkCapture(limit=20)
    provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model}, model_profile=profile,
        synthesis_profile=profile, max_tokens=2048, callbacks=(capture,))
    results = []
    for index, item in enumerate(selected):
        row = cases[item["case_id"]]
        args = prepare(row)
        for enabled in ((False, True) if index % 2 == 0 else (True, False)):
            before = len(capture.calls)
            start = time.perf_counter()
            record = {"case_id": row["case_id"], "language": row["language"], "enabled": enabled}
            try:
                cascade = CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(provider),
                    encoder=_target_encoder(ROOT, language=row["language"]) if enabled else None)
                proposal = await asyncio.wait_for(cascade(*args), timeout=90)
                RoutePolicy().accept(proposal, args[1], args[3])
                record["proposal"] = asdict(proposal)
            except Exception as exc:
                record["error"] = {"type": type(exc).__name__, "message": str(exc)}
            record["latency_ms"] = (time.perf_counter()-start)*1000
            record["calls"] = [{k: call[k] for k in ("raw_output", "usage", "latency_ms", "error_type") if k in call}
                               for call in capture.calls[before:]]
            results.append(record)
            with (OUT / "results.jsonl").open("a") as target:
                target.write(json.dumps(record, ensure_ascii=False, default=lambda v: v.value) + "\n")
            print(row["case_id"], enabled, len(record["calls"]), record.get("error", "OK"), flush=True)
    summary = {str(enabled): {"model_calls": sum(len(row["calls"]) for row in results if row["enabled"] == enabled),
        "total_ms": sum(row["latency_ms"] for row in results if row["enabled"] == enabled),
        "errors": sum("error" in row for row in results if row["enabled"] == enabled)} for enabled in (False, True)}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(main())
