from __future__ import annotations

import asyncio
import json

from evaluation.command_primary_eval import (
    CheckResult,
    CostResult,
    EvalCase,
    EvaluationStatus,
    UnderstandingDirectResult,
    UnderstandingDirectRunner,
)


def test_understanding_direct_runner_writes_shared_artifacts(tmp_path) -> None:
    case = EvalCase(
        case_id="refund-continuation-001",
        message="还是没到账",
        initial_state={"active_flow": "refund_status"},
        expected={"command": "CONTINUE_FLOW"},
        slice="continuation",
    )

    class Adapter:
        version = "injected-understanding-v1"

        def __init__(self) -> None:
            self.seen = []

        async def evaluate(self, received: EvalCase) -> UnderstandingDirectResult:
            self.seen.append(received)
            return UnderstandingDirectResult(
                trigger=CheckResult(True, {
                    "expected": "INVOKED",
                    "actual": "INVOKED",
                }),
                artifact=CheckResult(True, {
                    "expected_command": received.expected["command"],
                    "actual_command": "CONTINUE_FLOW",
                }),
                consumption=CheckResult(True, {
                    "route_policy": "ACCEPTED",
                    "turn_plan": "COMPILED",
                }),
                outcome=CheckResult(True, {
                    "understanding_status": "RESOLVED",
                }),
                cost=CostResult(
                    latency_ms=12.5,
                    token_usage=0,
                    invocation_count=1,
                ),
            )

    adapter = Adapter()
    report = asyncio.run(UnderstandingDirectRunner(adapter).run(
        (case,),
        tmp_path,
        run_id="run-001",
        dataset_id="synthetic-contract-v1",
        split="dev",
        configuration={"flow_registry": "flow-registry-v1"},
    ))

    assert adapter.seen == [case]
    assert report.status is EvaluationStatus.PASS
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    ]

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    prediction = json.loads((tmp_path / "predictions.jsonl").read_text())
    persisted_report = json.loads((tmp_path / "report.json").read_text())

    assert manifest["adapter_version"] == "injected-understanding-v1"
    assert manifest["case_count"] == 1
    assert prediction["case_id"] == case.case_id
    assert prediction["trigger"]["passed"] is True
    assert prediction["artifact"]["detail"]["actual_command"] == "CONTINUE_FLOW"
    assert prediction["consumption"]["detail"] == {
        "route_policy": "ACCEPTED",
        "turn_plan": "COMPILED",
    }
    assert prediction["outcome"]["detail"]["understanding_status"] == "RESOLVED"
    assert prediction["cost"] == {
        "invocation_count": 1,
        "latency_ms": 12.5,
        "token_usage": 0,
    }
    assert persisted_report["dimensions"]["trigger"] == {
        "denominator": 1,
        "numerator": 1,
        "rate": 1.0,
    }
    assert persisted_report["dimensions"]["artifact"]["rate"] == 1.0
    assert persisted_report["dimensions"]["consumption"]["rate"] == 1.0
    assert persisted_report["dimensions"]["outcome"]["rate"] == 1.0
    assert persisted_report["dimensions"]["cost"]["total_latency_ms"] == 12.5
