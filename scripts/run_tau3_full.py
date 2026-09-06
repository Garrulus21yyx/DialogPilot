#!/usr/bin/env python3
"""Two complete retail development tasks through Target and the official evaluator."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from datetime import datetime, timezone
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
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_memory_fact_store import PostgresMemoryFactStore
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.target_runtime_composition import build_target_runtime
from memory.conversation_memory import MemoryManager
from mcp.tool_manager import MCPToolManager
from services.answer_verifier import AnswerVerifier


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


async def run(args):
    os.environ["TAU2_DATA_DIR"] = str(args.tau_source.resolve() / "data")
    os.environ["MODEL_CONTEXT_WINDOW_TOKENS"] = "64000"
    from loguru import logger
    logger.remove()
    from tau2.domains.retail.environment import get_environment, get_tasks
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.user.user_simulator import UserSimulator
    from tau2.evaluator.evaluator import evaluate_simulation, EvaluationType
    from evaluation.tau3_full_adapter import Tau3TargetAgent, ObservedVerifier, ModelDiagnostics, UserModelDiagnostics
    import litellm
    from evaluation.tau3_tool_binding import bind_environment

    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.WORKER)
    args.output.mkdir(parents=True, exist_ok=False)
    db_name = "dialogpilot_tau3_full_" + uuid.uuid4().hex[:12]
    parts = urlsplit(args.database_url)
    db_url = urlunsplit((parts.scheme, parts.netloc, "/" + db_name, parts.query, ""))
    tasks = get_tasks("train")[:2]
    manifest = {"status": "RUNNING", "split": "train", "task_ids": [t.id for t in tasks],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "project_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "tau_commit": subprocess.check_output(["git", "-C", str(args.tau_source), "rev-parse", "HEAD"], text=True).strip(),
                "configuration": "Target production application, one registered retail domain, encoder disabled",
                "max_steps": args.max_steps, "max_model_calls_per_work_item": 20,
                "model_context_budget": 64000, "worker_profile": profile.to_dict(),
                "user_model": args.user_model, "seed": 300,
                "user_thinking": "disabled",
                "evaluation": "official ALL plus ENV/ACTION diagnostics, strict replay",
                "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for folder in ("application", "infrastructure", "evaluation")
                                  for path in sorted((ROOT / folder).glob("*.py"))},
                "limitations": ["two development tasks are not heldout performance",
                                "no independent human-quality assessment", "no remote idempotency or atomic entity CAS API",
                                "text-only approval classifier is evaluation UI adaptation",
                                "HTTP/SSE and multiple domain routing not exercised"]}
    write(args.output / "manifest.json", manifest)
    with psycopg.connect(args.database_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    pool = None
    rows = []
    try:
        PostgresMigrationRunner(db_url).upgrade()
        pool = PostgresPool(PostgresPoolConfig(db_url))
        pool.open()
        with tempfile.TemporaryDirectory(prefix="tau3-full-redis-") as temp:
            socket = Path(temp) / "redis.sock"
            redis = subprocess.Popen(["redis-server", "--port", "0", "--unixsocket", str(socket),
                                      "--save", "", "--appendonly", "no"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if socket.exists():
                        break
                    await asyncio.sleep(.05)
                for task in tasks:
                    environment = get_environment()
                    agent = Tau3TargetAgent(environment, loop=asyncio.get_running_loop())
                    tools = MCPToolManager(api_key=values["ANTHROPIC_API_KEY"],
                                           base_url=policy.base_url, model=profile.model)
                    memory = MemoryManager(redis_url="unix://" + str(socket),
                        fact_store=PostgresMemoryFactStore(pool), api_key=values["ANTHROPIC_API_KEY"])
                    components = None
                    orchestrator = None
                    row = {"task_id": task.id, "official_reward": None}
                    try:
                        registry = bind_environment(environment, tools, agent.call_tool)
                        components = await build_target_runtime(
                            database_url=db_url, postgres_pool=pool, tool_manager=tools,
                            memory=memory, response_delivery=PostgresResponseDeliveryService(
                                pool, resume_binding_secret=uuid.uuid4().hex),
                            model_policy=policy, provider_config={"api_key": values["ANTHROPIC_API_KEY"],
                                                                 "callbacks": [ModelDiagnostics(agent.trace)],
                                                                 "base_url": policy.base_url},
                            project_root=ROOT, registry=registry, enable_encoder=False,
                            knowledge_verifier=ObservedVerifier(AnswerVerifier(client=tools.llm_client,
                                model_profile=policy.profile(ModelRole.VERIFIER)), agent.trace))
                        agent.configure(components, pool, tools.llm_client, profile)
                        litellm.callbacks = [UserModelDiagnostics(agent.trace)]
                        user = UserSimulator(llm=args.user_model, instructions=str(task.user_scenario),
                            llm_args={"api_key": values["ANTHROPIC_API_KEY"], "api_base": policy.base_url,
                                      "temperature": 0, "thinking": {"type": "disabled"},
                                      "max_tokens": 512, "timeout": 60, "num_retries": 0})
                        orchestrator = Orchestrator(
                            domain="retail", agent=agent, user=user, environment=environment,
                            task=task, max_steps=args.max_steps, seed=300)
                        simulation = await asyncio.to_thread(orchestrator.run)
                        write(args.output / f"task-{task.id}-trajectory.json", simulation.model_dump())
                        for evaluation in (EvaluationType.ALL, EvaluationType.ENV, EvaluationType.ACTION):
                            reward = await asyncio.to_thread(evaluate_simulation, simulation, task,
                                evaluation_type=evaluation, solo_mode=False, domain="retail", strict_replay=True)
                            row[evaluation.value] = reward.model_dump()
                            if evaluation is EvaluationType.ALL:
                                row["official_reward"] = reward.reward
                        row["termination"] = simulation.termination_reason.value
                        row["status"] = "EVALUATED"
                    except Exception as exc:
                        if orchestrator is not None:
                            write(args.output / f"task-{task.id}-partial-trajectory.json",
                                  [message.model_dump() for message in orchestrator.trajectory])
                        row.update(status="ERROR", error_type=type(exc).__name__,
                                   error=str(exc).replace(values["ANTHROPIC_API_KEY"], "[REDACTED]")[:240])
                    finally:
                        agent.stop()
                        row["target_trace"] = agent.trace
                        if components:
                            await components.checkpoint_owner.__aexit__(None, None, None)
                        await memory.close()
                        await tools.llm_client.close()
                    rows.append(row)
                    write(args.output / f"task-{task.id}.json", row)
                    print(json.dumps({key: value for key, value in row.items() if key != "target_trace"}, default=str), flush=True)
            finally:
                redis.terminate()
                await asyncio.to_thread(redis.wait)
    finally:
        if pool:
            pool.close()
        # Only the exact database created by this invocation is removed.
        with psycopg.connect(args.database_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))
        manifest["status"] = "EVALUATED" if len(rows) == 2 and all(r["status"] == "EVALUATED" for r in rows) else "INCOMPLETE"
        write(args.output / "manifest.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tau-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url", default="postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot")
    parser.add_argument("--user-model", default="anthropic/deepseek-v4-flash")
    parser.add_argument("--max-steps", type=int, default=80)
    asyncio.run(run(parser.parse_args()))
