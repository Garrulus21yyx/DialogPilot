"""Measure the real cascade/Policy, keeping model-call savings separate from quality."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import time

from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.target_conversation_manager import TargetContextMessage, TargetTurnContext
from application.target_encoder_artifact import load_target_text_encoder_artifact
from evaluation.legacy_capability_encoder import TargetEncoderUnderstanding
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from application.turn_planning import RoutePolicy, TurnProposal, ProposalDisposition


CAPABILITIES = {"knowledge_search": "general_qa", "refund_status": "refund_status_summary",
                "product_identification": "product_identification"}


def prepare(row):
    state = ConversationState.empty(tenant_id="encoder-eval", user_id="fixture-user", conversation_id=row["case_id"])
    # Identical, explicitly supplied fixture parameters in both arms and all
    # labels. This tests routing with available inputs, not entity extraction.
    obs = TurnObservations(row["text"], (("order_id", "DP2468"), ("asset_id", "IMG2468")))
    context = TargetTurnContext(recent_messages=tuple(TargetContextMessage(
        message["role"], message["content"], f"history:{index}")
        for index, message in enumerate(row.get("messages", ()))))
    context = replace(context, entity_bindings=EntityBindingResolver().resolve(obs, state, context))
    return obs, state, DeterministicResolver().resolve(obs, state), build_default_capability_registry(state.tenant_id), context


class CountingPlanner:
    """Control-flow probe only; never claims to simulate model latency/quality."""
    def __init__(self):
        self.calls = 0

    async def plan(self, *args):
        self.calls += 1
        return TurnProposal(ProposalDisposition.CLARIFY, (), "EVALUATION_PLANNER_REACHED")


async def evaluate(rows, artifacts, *, load_artifact=load_target_text_encoder_artifact, warmup=False):
    encoders = {language: TargetEncoderUnderstanding(load_artifact(path))
                for language, path in artifacts.items()}
    if warmup:
        for language, encoder in encoders.items():
            example = next(row for row in rows if row["language"] == language)
            args = prepare(example)
            await encoder(args[0], args[1], args[3], args[4])
    details = []
    for row in rows:
        args = prepare(row)
        planner_on, planner_off = CountingPlanner(), CountingPlanner()
        start = time.perf_counter()
        decision = await encoders[row["language"]](args[0], args[1], args[3], args[4])
        latency = (time.perf_counter() - start) * 1000
        on = await CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), planner_on,
                                               encoder=encoders[row["language"]])(*args)
        await CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), planner_off)(*args)
        selected, error = None, None
        if decision.accepted:
            try:
                RoutePolicy().accept(on, args[1], args[3])
                command, = on.commands
                selected = CAPABILITIES.get(command.tool_id or command.skill_id)
                parameters = {arg.name: json.loads(arg.value_json) for arg in command.arguments}
                assert all(parameters.get(k) == v for k, v in {
                    "refund_status_summary": {"order_id": "DP2468"},
                    "product_identification": {"asset_id": "IMG2468"},
                    "general_qa": {"query": row["text"]},
                }[selected].items())
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
        details.append(dict(case_id=row["case_id"], language=row["language"], source=row.get("source", "independent-challenge"),
            contextual=bool(row.get("messages")), expected=row["label"], accepted=decision.accepted,
            selected=selected, correct=selected == row["label"] and error is None if decision.accepted else None,
            reason=decision.reason_code, encoder_ms=latency, planner_on=planner_on.calls,
            planner_off=planner_off.calls, error=error))
    summary = {}
    for language in encoders:
        group = [row for row in details if row["language"] == language]
        accepted = [row for row in group if row["accepted"]]
        correct = sum(row["correct"] for row in accepted)
        times = sorted(row["encoder_ms"] for row in group)
        summary[language] = dict(total=len(group), accepted=len(accepted), correct=correct,
            accepted_precision=correct / len(accepted) if accepted else None,
            coverage=len(accepted) / len(group), planner_on=sum(row["planner_on"] for row in group),
            planner_off=sum(row["planner_off"] for row in group), encoder_median_ms=statistics.median(times),
            encoder_p95_ms=times[min(len(times)-1, int(len(times)*.95))],
            reasons=dict(Counter(row["reason"] for row in group)),
            false_accepts=[row["case_id"] for row in accepted if not row["correct"]],
            contextual_accepted=sum(row["contextual"] for row in accepted))
    return {"summary": summary, "details": details,
            "warmup": warmup,
            "scope": "Real FastPathPolicy + cascade + RoutePolicy with grounded parameter fixture; planner is counting stub, not real LLM latency or full task quality"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", type=Path, required=True)
    parser.add_argument("--zh", type=Path, required=True)
    parser.add_argument("--en", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("evaluation output already exists")
    rows = [json.loads(line) for path in args.cases for line in path.read_text().splitlines() if line.strip()]
    result = asyncio.run(evaluate(rows, {"zh": args.zh, "en": args.en}))
    result["inputs"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in args.cases}
    result["artifacts"] = {lang: hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()
                           for lang, path in {"zh": args.zh, "en": args.en}.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
