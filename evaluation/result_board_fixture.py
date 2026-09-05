"""Current ResultBoard observations for historical coverage scenarios.

Frozen samples described CoverageGate reports. This probe instead submits result
prefixes to the current owner and records its typed rejection. It never repairs an
invalid batch or returns a substitute runtime snapshot.
"""
from application.agent_result import AgentResult, AgentResultStatus
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.result_board import ResultBoard, ResultBoardError
from application.work_item import ControlMode, WorkItem, WorkPlan


def observe_result_submissions(required_ids, outcome_ids):
    plan = WorkPlan(tuple(WorkItem(
        work_item_id=task_id, owner_agent="general", objective="coverage probe",
        control_mode=ControlMode.DELEGATED, allowed_tools=("fixture_read",), allowed_skills=(),
        arguments=(), requirement_ids=(), dependencies=(),
        effect=CapabilityEffect.READ, risk=CapabilityRisk.LOW,
        expected_output_schema="agent-result-v1", verification_profile="fixture-v1",
        state_snapshot_version=0, registry_fingerprint="fixture-v1",
        timeout_seconds=1, max_steps=1,
    ) for task_id in required_ids), primary_work_item_id=required_ids[0])
    owner = ResultBoard()
    accepted = owner.evaluate(plan, ())
    submissions = []
    rejection = None
    for task_id in outcome_ids:
        result = AgentResult(task_id, "general", AgentResultStatus.SUCCEEDED,
                             "FIXTURE_SUCCESS", "fixture-v1")
        submissions.append(result)
        try:
            accepted = owner.evaluate(plan, tuple(submissions))
        except ResultBoardError as exc:
            rejection = {"work_item_id": task_id, "error": str(exc)}
            break
    accepted_ids = [item.work_item_id for item in accepted.results]
    rejected_id = rejection["work_item_id"] if rejection else None
    return {
        "owner": "application.result_board.ResultBoard.evaluate",
        "contract_migration": "legacy coverage report -> accepted prefix / rejected submission",
        "accepted_result_ids": accepted_ids,
        "successful_result_ids": [item.work_item_id for item in accepted.results
                                  if item.status is AgentResultStatus.SUCCEEDED],
        "pending_work_item_ids": [item.work_item_id for item in accepted.ready_items],
        "accepted_prefix_complete": accepted.complete,
        "submission_rejected": rejection is not None,
        "duplicate_rejected": rejection is not None and rejected_id in accepted_ids,
        "unplanned_rejected": rejection is not None and rejected_id not in required_ids,
        "rejection": rejection,
    }
