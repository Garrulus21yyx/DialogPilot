"""Run the registered 200-case PostgreSQL fault matrix and supporting gates.

TEST_DATABASE_URL must identify a test PostgreSQL instance with CREATE DATABASE
permission. pytest creates and deletes its own isolated database. No model calls.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


GATES = [
    "tests/test_write_recovery.py",
    "tests/test_registered_write_recovery_postgres.py",
    "tests/test_target_persistence_and_manager.py",
    "tests/test_target_architecture_contracts.py",
    "tests/test_write_workflow.py",
    "tests/test_customer_operations.py",
    "tests/test_customer_operations_tools.py",
    "tests/test_domain_action_approval.py",
    "tests/test_approval_revision_lifecycle.py",
    "tests/test_checkpoint_takeover.py",
    "tests/test_target_framework_agent.py::test_domain_input_resume_reuses_progress_after_postgres_checkpoint_reopen",
    "tests/test_target_framework_agent.py::test_postgres_subgraph_survives_process_exit_after_tool",
    "tests/test_postgres_response_delivery.py",
    "tests/test_delivery_contract.py",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("TEST_DATABASE_URL"):
        parser.error("TEST_DATABASE_URL is required; skipped PostgreSQL tests are not evidence")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repository = Path(__file__).resolve().parents[1]
    tracked_diff = subprocess.check_output(["git", "diff", "HEAD"], cwd=repository)
    paths = ["application/write_workflow.py", "infrastructure/postgres_target_runtime.py",
        "infrastructure/target_workflow_execution.py", "services/customer_operations.py",
        "mcp/customer_operations_tools.py", "infrastructure/langgraph_checkpoint.py",
        "infrastructure/postgres_response_delivery.py", "tests/test_controlled_business_faults.py",
        "scripts/run_controlled_business_faults.py"]
    paths.extend(["application/capability_registry.py", "application/default_capability_registry.py",
        "application/work_item.py", "application/conversation_state.py", "application/target_conversation_manager.py",
        "application/target_chat_application.py", "application/response_assembly.py",
        "infrastructure/postgres_write_recovery.py", "tests/test_write_recovery.py",
        "tests/test_registered_write_recovery_postgres.py"])
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "seed": 20260908,
        "planned_cases": 200, "recovery_call_budget": 4, "persisted_recovery_budget": 3, "llm_calls": 0,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip(),
        "tracked_diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
        "files": {p: hashlib.sha256((repository / p).read_bytes()).hexdigest() for p in paths},
        "scope": "Production PostgreSQL owners and tool adapters; injected tool-boundary exceptions. "
                 "Approval pauses rebuild runtime objects. This is not a 200-process-crash or HTTP/LLM benchmark.",
        "acceptance": "All 200 scenarios observed; at most one business row per operation; "
                      "missing receipts permit same-operation replay only under the pinned owner guarantee. "
                      "Recovery rate is measured, not assumed; budget exhaustion requires manual review."}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    env = {**os.environ, "PYTHONPATH": str(repository), "CONTROLLED_FAULT_ROWS": str(root / "cases.jsonl")}
    exit_codes = {}
    checks = {}
    unexpected_skips = []
    for name, tests in (("matrix", ["tests/test_controlled_business_faults.py"]), ("gates", GATES)):
        command = [sys.executable, "-m", "pytest", "-q", "--tb=short", *tests,
                   "--junitxml=" + str(root / (name + ".xml"))]
        with (root / (name + ".log")).open("w") as log:
            exit_codes[name] = subprocess.call(command, cwd=repository, env=env, stdout=log, stderr=subprocess.STDOUT)
        xml = root / (name + ".xml")
        if xml.exists():
            tree = ET.parse(xml).getroot()
            suites = tree.iter("testsuite")
            counts = Counter()
            for suite in suites:
                for key in ("tests", "failures", "errors", "skipped"):
                    counts[key] += int(suite.get(key, "0"))
            checks[name] = dict(counts)
            skips = [{"test": case.get("name"), "reason": case.find("skipped").get("message", "")}
                     for case in tree.iter("testcase") if case.find("skipped") is not None]
            checks[name]["skip_details"] = skips
            unexpected_skips.extend(skip for skip in skips if skip["reason"] !=
                "In-memory ledgers are isolated by instance, not database scope")
        print(name, exit_codes[name], checks.get(name, {}), flush=True)
    rows_file = root / "cases.jsonl"
    rows = [json.loads(line) for line in rows_file.read_text().splitlines()] if rows_file.exists() else []
    summary = {"planned": 200, "observed": len(rows),
        "automatic_recoveries": sum(r["recovered"] for r in rows),
        "automatic_recovery_rate": sum(r["recovered"] for r in rows) / 200,
        "duplicate_business_rows": sum(max(0, r["business_rows"] - 1) for r in rows),
        "business_rows": sum(r["business_rows"] for r in rows),
        "scenarios": {name: {"count": sum(r["scenario"] == name for r in rows),
            "recovered": sum(r["scenario"] == name and r["recovered"] for r in rows)}
            for name in sorted({r["scenario"] for r in rows})},
        "exit_codes": exit_codes, "checks": checks,
        "valid_run": len(rows) == 200 and len({r["case_id"] for r in rows}) == 200
            and all(code == 0 for code in exit_codes.values())
            and not unexpected_skips,
        "finished_at": datetime.now(timezone.utc).isoformat()}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["valid_run"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
