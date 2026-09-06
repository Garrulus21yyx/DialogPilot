#!/usr/bin/env python3
"""Probe two τ³ retail train openings against the real Target composition.

Uses isolated PostgreSQL/Redis and the configured model. No task expectations,
customer identity from scenario, synthetic business state, or fabricated reward
are passed to DialogPilot. Tool/policy integration remains explicitly unscored.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
import psycopg
from psycopg import sql

from core.model_policy import ModelPolicy, ModelRole
from evaluation.chat_application_runner import ChatApplicationRunner
from evaluation.tau3_adapter import Tau3ChatBridge, capability_overlap
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_memory_fact_store import PostgresMemoryFactStore
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.target_runtime_composition import build_target_runtime
from memory.conversation_memory import MemoryManager
from mcp.tool_manager import MCPToolManager


class ProbeTools(MCPToolManager):
    """Record actual denied/unavailable calls; do not replace them with fixtures."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []

    async def execute_for_agent(self, name, params, **kwargs):
        result = await super().execute_for_agent(name, params, **kwargs)
        self.calls.append({"name": name, "params": params, "result": asdict(result)})
        return result


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


async def run(args):
    # Set before importing τ³: its installed package does not bundle data.
    os.environ["TAU2_DATA_DIR"] = str(args.tau_source.resolve() / "data")
    from tau2.domains.retail.environment import get_environment, get_tasks
    from tau2.data_model.message import AssistantMessage
    from tau2.user.user_simulator import UserSimulator
    from loguru import logger
    logger.remove()  # SDK errors can contain endpoint configuration; record type only.

    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.INTENT)
    args.output.mkdir(parents=True, exist_ok=False)
    tasks = get_tasks("train")[:2]
    run_id = uuid.uuid4().hex[:12]
    database_name = "dialogpilot_tau3_" + run_id
    parts = urlsplit(args.admin_database_url)
    db_url = urlunsplit((parts.scheme, parts.netloc, "/" + database_name, parts.query, ""))
    manifest = {
        "status": "RUNNING", "scope": "ENTRY_PROBE_NOT_OFFICIAL_TASK_EVALUATION",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "tau_commit": subprocess.check_output(["git", "-C", str(args.tau_source), "rev-parse", "HEAD"], text=True).strip(),
        "domain": "retail", "split": "train", "task_ids": [task.id for task in tasks],
        "turns_per_task": 1, "trials": 1, "official_reward": None,
        "planner_profile": profile.to_dict(),
        "user_model": args.user_model, "user_seed": 300,
        "policy_injected": False, "business_tools_bound": False,
        "excluded": ["official task completion scoring", "business tool compatibility",
                     "policy compliance", "HTTP/SSE", "background worker dispatch"],
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in [ROOT / "scripts/run_tau3_entry_smoke.py", ROOT / "evaluation/tau3_adapter.py"]},
    }
    write_json(args.output / "manifest.json", manifest)
    with psycopg.connect(args.admin_database_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    platform = memory = components = tools = None
    rows = []
    try:
        PostgresMigrationRunner(db_url).upgrade()
        platform = PostgresPool(PostgresPoolConfig(db_url))
        platform.open()
        with tempfile.TemporaryDirectory(prefix="tau3-redis-") as temp:
            socket = Path(temp) / "redis.sock"
            process = subprocess.Popen(["redis-server", "--port", "0", "--unixsocket", str(socket),
                "--save", "", "--appendonly", "no"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if socket.exists():
                        break
                    if process.poll() is not None:
                        raise RuntimeError("isolated Redis exited")
                    await asyncio.sleep(.05)
                if not socket.exists():
                    raise TimeoutError("Redis startup")
                tools = ProbeTools(api_key=values["ANTHROPIC_API_KEY"], base_url=policy.base_url,
                                   model=profile.model)
                memory = MemoryManager(redis_url="unix://" + str(socket),
                    fact_store=PostgresMemoryFactStore(platform), api_key=values["ANTHROPIC_API_KEY"])
                delivery = PostgresResponseDeliveryService(platform, resume_binding_secret=uuid.uuid4().hex)
                components = await build_target_runtime(
                    database_url=db_url, postgres_pool=platform, tool_manager=tools,
                    memory=memory, response_delivery=delivery, model_policy=policy,
                    provider_config={"api_key": values["ANTHROPIC_API_KEY"], "base_url": policy.base_url},
                    project_root=ROOT,
                )
                environment = get_environment()
                manifest["capability_preflight"] = capability_overlap(components.registry, environment.get_tools())
                write_json(args.output / "manifest.json", manifest)
                runner = ChatApplicationRunner(lambda overrides: components.application)
                for task in tasks:
                    row = {"task_id": task.id, "official_reward": None}
                    try:
                        # Only the simulator gets the scenario. Agent receives its generated text.
                        simulator = UserSimulator(llm=args.user_model, instructions=str(task.user_scenario),
                            llm_args={"api_key": values["ANTHROPIC_API_KEY"], "api_base": policy.base_url,
                                      "temperature": 0.0, "max_tokens": 512, "timeout": 60,
                                      "num_retries": 0, "seed": 300})
                        message, _ = await asyncio.to_thread(simulator.generate_next_message,
                            AssistantMessage(role="assistant", content="Hi! How can I help you today?"),
                            simulator.get_init_state())
                        row["user_usage"] = message.usage
                        bridge = Tau3ChatBridge(runner, tenant_id="default",
                            conversation_id=f"tau3-{run_id}-{task.id}", user_id="benchmark-visitor")
                        before = len(tools.calls)
                        row.update(await asyncio.wait_for(bridge.receive(message.content), timeout=120))
                        row["tool_calls"] = tools.calls[before:]
                    except Exception as exc:
                        row["error_type"] = type(exc).__name__
                    rows.append(row)
                    write_json(args.output / f"task-{task.id}.json", row)
                    print("task", task.id, row.get("outcome_type", row.get("error_type")), flush=True)
            finally:
                if components:
                    await components.checkpoint_owner.__aexit__(None, None, None)
                if memory:
                    await memory.close()
                if tools:
                    await tools.llm_client.close()
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        manifest.update(status="PROBED", completed_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        manifest.update(status="INFRASTRUCTURE_FAILED", error_type=type(exc).__name__)
        raise RuntimeError("entry smoke failed: " + type(exc).__name__) from None
    finally:
        if platform:
            platform.close()
        # This exact database was created by this invocation, never a supplied business DB.
        with psycopg.connect(args.admin_database_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
        write_json(args.output / "manifest.json", manifest)
        write_json(args.output / "report.json", {
            "scope": manifest["scope"], "attempted": len(rows),
            "completed_app_turns": sum(row.get("outcome_type") == "Completed" for row in rows),
            "official_reward": None, "accuracy": None,
            "full_benchmark_status": "BLOCKED_MISSING_BUSINESS_AND_POLICY_BINDINGS",
            "isolated_database_removed": True,
        })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tau-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--admin-database-url", default=os.getenv("TEST_DATABASE_URL"))
    parser.add_argument("--user-model", default="anthropic/deepseek-v4-flash")
    args = parser.parse_args()
    if not args.admin_database_url:
        parser.error("provide TEST_DATABASE_URL or --admin-database-url")
    asyncio.run(run(args))
