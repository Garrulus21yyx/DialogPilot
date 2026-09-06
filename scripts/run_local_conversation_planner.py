#!/usr/bin/env python3
"""Exercise the real ConversationAgent planner using local model inference."""

import os

for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
    os.environ[key] = "1"
import argparse
import asyncio
from dataclasses import asdict, replace
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.local_planning_client import LocalPlanningClient
from core.model_policy import ModelProfile
from evaluation.rag_ecommerce_dev import synthetic_development
from application.conversation_agent import ConversationAgent
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.target_conversation_manager import (
    TargetTurnContext,
    TargetContextMessage,
    TargetContextProjectionStatus,
)
from infrastructure.target_conversation_provider import (
    AnthropicConversationPlanningProvider,
)


async def run(args):
    if args.output.exists():
        raise ValueError("output must be new")
    import socket

    def denied(*_args, **_kwargs):
        raise RuntimeError("network disabled during local planning evaluation")

    socket.socket.connect = denied
    client = LocalPlanningClient(
        args.model,
        system_override=args.system_prompt.read_text() if args.system_prompt else None,
    )
    provider = AnthropicConversationPlanningProvider(
        client, model_profile=ModelProfile(str(args.model.resolve())), synthesis_profile=ModelProfile(str(args.model.resolve())), max_tokens=512
    )
    agent = ConversationAgent(provider)
    _, cases = synthetic_development()
    rows = []
    args.output.mkdir(parents=True)
    for case in cases:
        state = ConversationState.empty(
            tenant_id="local-eval", user_id="local-user", conversation_id=case.case_id
        )
        observations = TurnObservations(case.query, ())
        resolution = DeterministicResolver().resolve(observations, state)
        history = tuple(
            TargetContextMessage(
                "user" if i % 2 == 0 else "assistant",
                text,
                f"local:{case.case_id}:{i}",
                i + 1,
            )
            for i, text in enumerate(case.history)
        )
        context = TargetTurnContext(
            recent_messages=history,
            projection_status=TargetContextProjectionStatus.READY,
            source_watermark=len(history),
            projection_reason_codes=(),
        )
        context = replace(
            context,
            entity_bindings=EntityBindingResolver().resolve(
                observations, state, context
            ),
        )
        before = len(client.captures)
        proposal = await agent.plan(
            observations,
            state,
            resolution,
            build_default_capability_registry("local-eval"),
            context,
        )
        row = {
            "case_id": case.case_id,
            "query": case.query,
            "history": case.history,
            "deterministic_resolution": resolution.kind.value,
            "proposal": asdict(proposal),
            "model_calls": client.captures[before:],
        }
        rows.append(row)
        with (args.output / "cases.jsonl").open("a") as stream:
            stream.write(
                json.dumps(row, ensure_ascii=False, default=lambda x: x.value) + "\n"
            )
        print(case.case_id, proposal.disposition.value, flush=True)
    report = {
        "scope": "real ConversationAgent planning with local model; no retrieval, execution, generation quality or production-provider equivalence claimed",
        "synthetic_cases": len(rows),
        "model_identity": client.identity,
        "model_path": str(args.model.resolve()),
        "model_calls": len(client.captures),
        "external_inference_api_calls": 0,
        "network_disabled": True,
        "semantic_scores": "unreviewed",
        "system_prompt_override": str(args.system_prompt)
        if args.system_prompt
        else None,
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--system-prompt", type=Path)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(run(p.parse_args()))
