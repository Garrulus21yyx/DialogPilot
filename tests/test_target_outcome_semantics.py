"""Cross-product checks: work completion, evidence coverage and answer checks differ."""
import hashlib
from itertools import product
from types import SimpleNamespace

import pytest

from application.agent_result import AgentResultStatus
from application.response_assembly import AssembledResponse, ResponseAssemblyMode
from application.result_board import ResultBoardSnapshot


@pytest.mark.parametrize("statuses", product(AgentResultStatus, repeat=2))
@pytest.mark.parametrize("all_returned,missing,conflicts", [
    (True, (), ()), (False, (), ()),
    (True, ("order.state",), ()), (True, (), ("order:1",)),
])
def test_work_completion_requires_every_success_and_coverage(statuses, all_returned, missing, conflicts):
    board = ResultBoardSnapshot(
        tuple(SimpleNamespace(status=status) for status in statuses),
        (), (), (), missing, conflicts, all_returned, False,
    )
    assert board.coverage_complete == (not missing and not conflicts)
    assert board.task_completed == (
        all_returned and not missing and not conflicts
        and all(status is AgentResultStatus.SUCCEEDED for status in statuses)
    )


@pytest.mark.parametrize("status", ["PASS", "NOT_CHECKED", "PENDING", "UNKNOWN", "REJECT"])
@pytest.mark.parametrize("bound", [True, False])
def test_answer_attestation_needs_check_and_exact_text_binding(status, bound):
    text = "Your request could not be completed."
    response = AssembledResponse(
        text, ResponseAssemblyMode.TEMPLATE, (), False, status, "test",
        hashlib.sha256(text.encode()).hexdigest() if bound else "",
    )
    assert response.verified == (status == "PASS" and bound)


def test_checked_answer_can_explain_unsuccessful_work():
    board = ResultBoardSnapshot(
        (SimpleNamespace(status=AgentResultStatus.BLOCKED),), (), (), (), (), (), True, False,
    )
    text = "The service is unavailable."
    response = AssembledResponse(text, ResponseAssemblyMode.PASS_THROUGH, (), False,
        "PASS", "ANSWER_SUPPORT_CHECKED", hashlib.sha256(text.encode()).hexdigest())
    assert response.verified
    assert board.coverage_complete
    assert not board.task_completed
