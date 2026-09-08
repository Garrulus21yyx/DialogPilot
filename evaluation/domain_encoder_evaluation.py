"""Measure domain dispatch through the real cascade and compiler, not business success."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import statistics
import time

from application.conversation_state import ConversationState
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.default_capability_registry import build_default_capability_registry
from application.target_conversation_manager import TargetContextMessage, TargetTurnContext
from application.target_encoder_understanding import TargetEncoderUnderstanding
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from application.turn_planning import RoutePolicy, TurnPlanCompiler
from core.identity import IdentityFactory
from evaluation.encoder_fastpath_evaluation import CountingPlanner
from infrastructure.target_domain_encoder import TargetDomainEncoder


async def evaluate(rows, artifacts, *, device="cuda"):
    encoders = {language: TargetEncoderUnderstanding(TargetDomainEncoder(path, device=device, evaluation=True))
                for language, path in artifacts.items()}
    details = []
    for row in rows:
        state = ConversationState.empty(tenant_id="domain-eval", user_id="fixture", conversation_id=row["case_id"])
        registry = build_default_capability_registry(state.tenant_id)
        context = TargetTurnContext(recent_messages=tuple(TargetContextMessage(
            m["role"], m["content"], f"history:{i}") for i, m in enumerate(row.get("messages", ()))))
        obs = TurnObservations(row["text"])
        encoder = encoders[row["language"]]
        started = time.perf_counter()
        decision = await encoder(obs, state, registry, context)
        latency = (time.perf_counter() - started) * 1000
        planner = CountingPlanner()
        proposal = await CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), planner, encoder=encoder)(
            obs, state, DeterministicResolver().resolve(obs, state), registry, context)
        owner, error = None, None
        if decision.accepted:
            try:
                invocation = IdentityFactory(lambda: "fixed").create_invocation(
                    tenant_id=state.tenant_id, user_id=state.user_id, conversation_id=state.conversation_id, request_id="r")
                plan = TurnPlanCompiler().compile(RoutePolicy().accept(proposal, state, registry), state, registry, invocation)
                item, = plan.work.items
                owner = item.owner_agent
                assert item.objective == row["text"] and not item.arguments
            except (ValueError, AssertionError) as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
        details.append({"case_id": row["case_id"], "language": row["language"], "expected": row["label"],
            "accepted": decision.accepted, "owner": owner, "correct": owner == row["label"] and error is None,
            "reason": decision.reason_code, "contextual": bool(row.get("messages")), "encoder_ms": latency,
            "planner_calls": planner.calls, "error": error,
            "relation": row.get("relation", "unlabelled"), "history_length": len(row.get("messages", ()))})
    summary = {}
    for language in encoders:
        values = [r for r in details if r["language"] == language]
        accepted = [r for r in values if r["accepted"]]
        times = sorted(r["encoder_ms"] for r in values[1:])  # first call is warmup
        summary[language] = {"total": len(values), "accepted": len(accepted),
            "correct": sum(r["correct"] for r in accepted),
            "accepted_precision": sum(r["correct"] for r in accepted) / len(accepted) if accepted else None,
            "coverage": len(accepted) / len(values), "planner_calls_saved": len(values) - sum(r["planner_calls"] for r in values),
            "contextual_accepted": sum(r["contextual"] for r in accepted),
            "false_accepts": [r["case_id"] for r in accepted if not r["correct"]],
            "encoder_median_ms": statistics.median(times), "encoder_p95_ms": times[min(len(times)-1, int(len(times)*.95))]}
        for field in ("relation", "history_length"):
            summary[language]["by_" + field] = {
                str(key): {"total": sum(r[field] == key for r in values),
                           "accepted": sum(r[field] == key and r["accepted"] for r in values),
                           "correct": sum(r[field] == key and r["accepted"] and r["correct"] for r in values)}
                for key in sorted({r[field] for r in values})}
    return {"scope": "Actual domain encoder, cascade, Policy and WorkPlan compiler; no tool/LLM task execution. Empty task state; planner is counting stub.",
            "device": device, "summary": summary, "details": details}


async def main(zh: Path, en: Path, cases, output: Path, device="cuda"):
    if output.exists():
        raise ValueError("domain evaluation output exists")
    rows = [json.loads(line) for p in cases for line in p.read_text().splitlines()]
    result = await evaluate(rows, {"zh": zh, "en": en}, device=device)
    result["sources"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in cases}
    result["models"] = {lang: hashlib.sha256((p / "manifest.json").read_bytes()).hexdigest() for lang, p in (("zh", zh), ("en", en))}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zh", type=Path, required=True)
    parser.add_argument("--en", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    asyncio.run(main(**vars(parser.parse_args())))
