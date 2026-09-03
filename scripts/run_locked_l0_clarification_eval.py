#!/usr/bin/env python3
"""Run the locked L0 clarification slice with the live structured command model."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.model_policy import ModelPolicy, ModelRole  # noqa: E402
from evaluation.command_primary_eval.locked_l0_chat_runtime import (  # noqa: E402
    run_locked_l0_chat_eval,
)
from evaluation.command_primary_eval.locked_l0_artifacts import (  # noqa: E402
    write_locked_l0_not_run,
)
from evaluation.command_primary_eval.locked_l0_clarification import (  # noqa: E402
    LiveProviderIdentity,
)
from infrastructure.anthropic_command_completion import (  # noqa: E402
    AnthropicCommandCompletion,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "data/eval/dialogpilot-synthetic-contract-v1",
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> dict[str, object]:
    run_id = str(args.run_id or uuid.uuid4().hex)
    try:
        from anthropic import AsyncAnthropic
        from dotenv import dotenv_values
    except ImportError:
        return dict(
            write_locked_l0_not_run(
                dataset_root=args.dataset,
                output_dir=args.output,
                run_id=run_id,
                reason_code="LIVE_PROVIDER_DEPENDENCY_MISSING",
            )
        )
    file_env = {
        str(key): str(value)
        for key, value in dotenv_values(args.env_file).items()
        if value is not None
    }
    env = {**file_env, **os.environ}
    key = env.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return dict(
            write_locked_l0_not_run(
                dataset_root=args.dataset,
                output_dir=args.output,
                run_id=run_id,
                reason_code="LIVE_PROVIDER_KEY_MISSING",
            )
        )
    policy = ModelPolicy.from_env(env)
    profile = policy.profile(ModelRole.INTENT)
    identity = LiveProviderIdentity(
        provider=profile.provider,
        model=profile.model,
        model_profile_fingerprint=_fingerprint(
            {
                "provider": policy.provider,
                "base_url": policy.base_url or "official",
                "profile": profile.to_dict(),
            }
        ),
        sdk_version=version("anthropic"),
        real_provider=True,
    )
    client_kwargs = {
        "api_key": key,
        "max_retries": 0,
        "timeout": args.timeout_seconds,
    }
    if policy.base_url:
        client_kwargs["base_url"] = policy.base_url
    client = AsyncAnthropic(**client_kwargs)
    completion = AnthropicCommandCompletion(client, profile)
    try:
        return await run_locked_l0_chat_eval(
            dataset_root=args.dataset,
            output_dir=args.output,
            run_id=run_id,
            completion=completion,
            provider=identity,
            case_ids=tuple(args.case_id),
        )
    finally:
        await client.close()


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    report = asyncio.run(run(parse_args(argv)))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report["run_status"] != "COMPLETED":
        return 2
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
