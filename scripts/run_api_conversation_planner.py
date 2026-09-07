#!/usr/bin/env python3
"""Calibrate the production ConversationAgent planner using configured API inference."""

import os
import argparse
import asyncio
from dataclasses import asdict, replace
import json
import hashlib
from datetime import datetime, timezone
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelRole
from core.framework_models import conversation_models
from evaluation.framework_capture import FrameworkCapture
from time import perf_counter
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


class CapturingClient:
    """Capture text requests only; credentials and transport errors are never serialized."""
    def __init__(self, transport, *, limit, system_override=None):
        self.transport = transport
        self.limit = limit
        self.system_override = system_override
        self.messages = self
        self.captures = []

    async def create(self, **request):
        if len(self.captures) >= self.limit:
            raise RuntimeError("calibration API call limit reached")
        if self.system_override is not None:
            request = {**request, "system": self.system_override}
        capture = {"request": request}
        self.captures.append(capture)
        start = perf_counter()
        try:
            response = await self.transport.messages.create(**request)
            capture.update(raw_output=response.model_dump(mode="json"), usage=response.usage.model_dump(mode="json"))
            return response
        except Exception as exc:
            capture["error_type"] = type(exc).__name__
            raise RuntimeError("calibration transport failed: " + type(exc).__name__) from None
        finally:
            capture["latency_ms"] = (perf_counter() - start) * 1000


async def run(args):
    if args.output.exists():
        raise ValueError("output must be new")
    values = {k: str(v) for k, v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.INTENT)
    options = dict(api_key=values['ANTHROPIC_API_KEY'], max_retries=0, timeout=60.0)
    if policy.base_url:
        options['base_url'] = policy.base_url
    if args.system_prompt:
        raise ValueError("production planner evaluation uses the production prompt")
    client = FrameworkCapture(limit=20)
    provider = AnthropicConversationPlanningProvider(
        conversation_models(policy, options), model_profile=profile,
        synthesis_profile=policy.profile(ModelRole.SYNTHESIS), max_tokens=800, callbacks=(client,)
    )
    agent = ConversationAgent(provider)
    _, cases = synthetic_development()
    if args.business_controls:
        cases = tuple(SimpleNamespace(case_id="control:" + name, query=query, history=()) for name, query in (
            ("refund-status", "帮我看看订单 DP1234 的退款现在到哪一步了"),
            ("logistics", "帮我看看订单 DP1234 的包裹现在走到哪儿了"),
            ("cancel", "请帮我取消订单 DP1234"),
            ("refund-action", "请帮我为订单 DP1234 发起退款申请"),
        ))
    rows = []
    args.output.mkdir(parents=True)
    (args.output / "manifest.json").write_text(json.dumps({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model_profile": profile.to_dict(),
        "provider_version": provider.version,
        "max_calls": 20,
        "sdk_max_retries": 0,
        "request_timeout_seconds": 60,
        "synthetic_development_only": True,
        "business_controls": args.business_controls,
        "source_sha256": {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in ("scripts/run_api_conversation_planner.py",
                         "infrastructure/target_conversation_provider.py",
                         "application/conversation_agent.py",
                         "evaluation/rag_ecommerce_dev.py")
        },
    }, ensure_ascii=False, indent=2) + "\n")
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
        before = len(client.calls)
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
            "model_calls": client.calls[before:],
        }
        rows.append(row)
        with (args.output / "cases.jsonl").open("a") as stream:
            stream.write(
                json.dumps(row, ensure_ascii=False, default=lambda x: x.value) + "\n"
            )
        print(case.case_id, proposal.disposition.value, flush=True)
    report = {
        "scope": "production ConversationAgent and provider prompt via configured API; planning only, no retrieval or answer-quality claim",
        "synthetic_cases": len(rows),
        "model_profile": profile.to_dict(),
        "model_calls": len(client.calls),
        "external_inference_api_calls": len(client.calls),
        "usage": {key: sum(c.get("usage", {}).get(key, 0) or 0 for c in client.calls) for key in ("input_tokens", "output_tokens")},
        "semantic_scores": "unreviewed",
        "system_prompt_override": str(args.system_prompt) if args.system_prompt else None,
    }
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--system-prompt", type=Path, help="Explicit evaluation-only prompt override")
    p.add_argument("--business-controls", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(run(p.parse_args()))
