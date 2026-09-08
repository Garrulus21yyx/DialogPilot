#!/usr/bin/env python3
"""Selected retail development tasks through Target and the official evaluator."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from datetime import datetime, timezone
import json
import logging
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
from core.framework_models import framework_model
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_memory_fact_store import PostgresMemoryFactStore
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.target_runtime_composition import build_target_runtime
from memory.conversation_memory import MemoryManager
from mcp.tool_manager import MCPToolManager
from services.answer_verifier import AnswerVerifier
from infrastructure.langfuse_trace_sink import LangfuseTraceSink


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


async def score_simulation(simulation, task, *, evaluator, evaluations):
    """Independent official checks: a judge outage must not erase DB/action scores."""
    from core.tracing import exception_chain
    scores = {"official_reward": None, "evaluation_errors": {}}
    for evaluation in evaluations:
        try:
            reward = await asyncio.to_thread(
                evaluator, simulation, task, evaluation_type=evaluation,
                solo_mode=False, domain="retail", strict_replay=True)
        except Exception as exc:
            scores["evaluation_errors"][evaluation.value] = {
                "stage": "evaluation", "evaluation_type": evaluation.value,
                "exception_chain": exception_chain(exc),
            }
        else:
            scores[evaluation.value] = reward.model_dump()
            if evaluation.value == "all":
                scores["official_reward"] = reward.reward
    return scores


async def run(args):
    os.environ["TAU2_DATA_DIR"] = str(args.tau_source.resolve() / "data")
    os.environ["MODEL_CONTEXT_WINDOW_TOKENS"] = "64000"
    from loguru import logger
    logger.remove()
    from tau2.domains.retail.environment import get_environment, get_tasks
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.user.user_simulator import UserSimulator
    from tau2.evaluator.evaluator import evaluate_simulation, EvaluationType
    from evaluation.tau3_full_adapter import Tau3TargetAgent, ObservedVerifier
    from evaluation.tau3_tool_binding import bind_environment
    from evaluation.tau3_user_diagnostics import SimulatorDiagnostics, simulator_parameters

    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    for key, value in values.items():
        if key.startswith("LANGFUSE_") and value is not None:
            os.environ.setdefault(key, value)
    if args.completion_budget is not None:
        for role in (ModelRole.WORKER, ModelRole.INTENT, ModelRole.SYNTHESIS, ModelRole.VERIFIER):
            values[f"MODEL_{role.value.upper()}_MIN_COMPLETION_TOKENS"] = str(args.completion_budget)
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.WORKER)
    args.output.mkdir(parents=True, exist_ok=False)
    # Preserve existing owner-level error traces for failed end-to-end runs.
    # No local variable dumps: provider credentials must not enter artifacts.
    error_sink = logger.add(args.output / "errors.log", level="ERROR", diagnose=False, backtrace=False)
    standard_errors = logging.FileHandler(args.output / "application-errors.log")
    standard_errors.setLevel(logging.ERROR)
    logging.getLogger("application").addHandler(standard_errors)
    logging.getLogger("infrastructure").addHandler(standard_errors)
    db_name = "dialogpilot_tau3_full_" + uuid.uuid4().hex[:12]
    parts = urlsplit(args.database_url)
    db_url = urlunsplit((parts.scheme, parts.netloc, "/" + db_name, parts.query, ""))
    tasks = get_tasks("train")[args.task_offset:args.task_offset + args.task_count]
    if len(tasks) != args.task_count:
        raise ValueError("requested task range exceeds the development split")
    from infrastructure.target_agent_middleware import InteractionBoundaryMiddleware
    manifest = {"status": "RUNNING", "split": "train", "task_ids": [t.id for t in tasks],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "project_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "tracked_worktree_dirty": bool(subprocess.check_output(
                    ["git", "status", "--porcelain", "--untracked-files=no"], text=True).strip()),
                "tau_commit": subprocess.check_output(["git", "-C", str(args.tau_source), "rev-parse", "HEAD"], text=True).strip(),
                "configuration": "Target production application, one registered retail domain, encoder disabled",
                "max_steps": args.max_steps, "max_model_calls_per_work_item": 20,
                "max_domain_outcome_reviews_per_segment": InteractionBoundaryMiddleware.max_review_calls,
                "domain_outcome_review_profile": policy.profile(ModelRole.VERIFIER).to_dict(),
                "domain_outcome_review_max_tokens": policy.profile(ModelRole.VERIFIER).request(max_tokens=4096)["max_tokens"],
                "domain_outcome_review_scope": ["COMPLETE", "NEEDS_USER_INPUT", "BLOCKED", "PREPARE_ACTION"],
                "model_context_budget": 64000, "worker_profile": profile.to_dict(),
                "user_model": args.user_model, "seed": 300,
                "user_thinking": "disabled",
                "completion_budget_override": args.completion_budget,
                "user_max_tokens": args.user_max_tokens,
                "evaluation": "official ALL plus ENV/ACTION diagnostics, strict replay",
                "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for folder in ("core", "application", "infrastructure", "evaluation", "scripts")
                                  for path in sorted((ROOT / folder).glob("*.py"))},
                "limitations": ["selected development tasks are not heldout performance",
                                "no independent human-quality assessment", "no remote idempotency or atomic entity CAS API",
                                "text-only approval classifier is evaluation UI adaptation",
                                "HTTP/SSE and multiple domain routing not exercised"]}
    write(args.output / "manifest.json", manifest)
    with psycopg.connect(args.database_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    pool = None
    langfuse_sink = None
    user_diagnostics = None
    rows = []
    try:
        langfuse_sink = LangfuseTraceSink.from_env()
        user_diagnostics = SimulatorDiagnostics(args.output / 'simulator-calls',
            langfuse=langfuse_sink is not None,
            secrets=[v for k, v in values.items() if any(part in k for part in ('KEY', 'SECRET', 'PASSWORD'))])
        manifest["langfuse_enabled"] = langfuse_sink is not None
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
                                                                 "base_url": policy.base_url},
                            langfuse_sink=langfuse_sink,
                            project_root=ROOT, registry=registry, enable_encoder=False, response_locale="en",
                            knowledge_verifier=ObservedVerifier(AnswerVerifier(
                                framework_model(
                                    policy.profile(ModelRole.VERIFIER), {"api_key": values["ANTHROPIC_API_KEY"], "base_url": policy.base_url}, max_tokens=4096),
                                model_profile=policy.profile(ModelRole.VERIFIER),
                                callbacks=(langfuse_sink.callback(),) if langfuse_sink else ()), agent.trace))
                        manifest["domain_outcome_review_available_tokens"] = {
                            owner: worker._review_available_tokens
                            for owner, worker in components.orchestration._domain_workers.items()
                        }
                        manifest["context_protocol_reserve_tokens"] = int(os.getenv(
                            "CONTEXT_PROTOCOL_RESERVE_TOKENS", "600"))
                        write(args.output / "manifest.json", manifest)
                        agent.configure(components, pool, framework_model(profile,
                            {"api_key": values["ANTHROPIC_API_KEY"], "base_url": policy.base_url},
                            max_tokens=200), callbacks=(langfuse_sink.callback(),) if langfuse_sink else ())
                        row["langfuse_session_id"] = agent.conversation_id if langfuse_sink else None
                        user_parameters = simulator_parameters(args.user_max_tokens)
                        user = UserSimulator(llm=args.user_model, instructions=str(task.user_scenario),
                            llm_args={"api_key": values["ANTHROPIC_API_KEY"], "api_base": policy.base_url,
                                      **user_parameters, **user_diagnostics.options(task.id, agent.conversation_id,
                                          model=args.user_model, parameters=user_parameters)})
                        orchestrator = Orchestrator(
                            domain="retail", agent=agent, user=user, environment=environment,
                            task=task, max_steps=args.max_steps, seed=300)
                        simulation = await asyncio.to_thread(orchestrator.run)
                        write(args.output / f"task-{task.id}-trajectory.json", simulation.model_dump())
                        row["termination"] = simulation.termination_reason.value
                        row["simulation_status"] = "COMPLETED"
                        row.update(await score_simulation(simulation, task,
                            evaluator=evaluate_simulation,
                            evaluations=(EvaluationType.ALL, EvaluationType.ENV, EvaluationType.ACTION)))
                        row["status"] = "EVALUATION_INCOMPLETE" if row["evaluation_errors"] else "EVALUATED"
                    except Exception as exc:
                        if orchestrator is not None:
                            row['failure_context'] = {key: str(getattr(orchestrator, key, ''))
                                                      for key in ('step_count', 'from_role', 'to_role')}
                            write(args.output / f"task-{task.id}-partial-trajectory.json",
                                  [message.model_dump() for message in orchestrator.trajectory])
                            # User state is updated before Orchestrator rejects an empty reply.
                            state = getattr(orchestrator, 'user_state', None)
                            if state is not None:
                                write(args.output / f'task-{task.id}-user-state.json',
                                      user_diagnostics.sanitize(state))
                        import traceback
                        row['error_stack'] = [{'file': frame.filename, 'line': frame.lineno,
                                               'function': frame.name}
                                              for frame in traceback.extract_tb(exc.__traceback__)]
                        row.update(status="ERROR", error_type=type(exc).__name__,
                                   error=user_diagnostics.sanitize(str(exc))[:240])
                        from core.tracing import exception_chain
                        row["exception_chain"] = exception_chain(exc)
                    finally:
                        row['simulator_diagnostics'] = await asyncio.to_thread(user_diagnostics.task_summary, task.id)
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
        if user_diagnostics:
            manifest['simulator_trace_flush'] = user_diagnostics.close()
        if langfuse_sink:
            langfuse_sink.close()
        if pool:
            pool.close()
        # Only the exact database created by this invocation is removed.
        with psycopg.connect(args.database_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))
        manifest["status"] = "EVALUATED" if len(rows) == len(tasks) and all(r["status"] == "EVALUATED" for r in rows) else "INCOMPLETE"
        write(args.output / "manifest.json", manifest)
        logger.remove(error_sink)
        logging.getLogger("application").removeHandler(standard_errors)
        logging.getLogger("infrastructure").removeHandler(standard_errors)
        standard_errors.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tau-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url", default="postgresql://dialogpilot:dialogpilot-local@localhost:15432/dialogpilot")
    parser.add_argument("--user-model", default="anthropic/deepseek-v4-flash")
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--completion-budget", type=int, default=None)
    parser.add_argument("--user-max-tokens", type=int, default=512)
    parser.add_argument("--task-offset", type=int, default=0)
    parser.add_argument("--task-count", type=int, default=2)
    args = parser.parse_args()
    if args.task_offset < 0 or args.task_count < 1:
        parser.error("task offset must be nonnegative and task count must be positive")
    asyncio.run(run(args))
