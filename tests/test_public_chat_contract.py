import pytest

from api.main import app
from application.chat_application import (
    Accepted,
    Cancelled,
    Completed,
    Conflict,
    Expired,
    Failed,
    HandedOff,
    NeedsInput,
    Reconciling,
    Rejected,
)
from application.public_chat_contract import project_chat_outcome


@pytest.mark.parametrize(
    ("outcome", "status", "action"),
    [
        (Completed("response", {"response_id": "response"}), 200, None),
        (Accepted("run", {"execution": "RUNNING"}), 202, "poll_same_request"),
        (NeedsInput("run", "signal", "approval", "later", "publication"), 202,
         "reply_to_signal_schema"),
        (Reconciling("run", {"waiting": True}, 1.0), 202, "poll_only"),
        (HandedOff("ticket", "handoff"), 200, "continue_with_human_support"),
        (Cancelled("run", "cancelled"), 200, "start_new_request_if_needed"),
        (Expired("run", "ADMISSION"), 410, "start_new_request"),
        (Conflict("IDEMPOTENCY_CONFLICT", {"invocation_key": "key"}), 409,
         "read_existing_or_change_request_id"),
        (Rejected("invalid", "correct input"), 422, "correct_request"),
        (Failed("unavailable", True, "trace"), 503, "retry_same_request"),
        (Failed("invariant", False, "trace"), 500, "contact_support"),
    ],
)
def test_chat_outcome_v1_has_one_http_and_client_action_projection(
    outcome, status, action,
):
    projection = project_chat_outcome(outcome)
    assert projection.status_code == status
    if action is None:
        assert projection.body == {"response_id": "response"}
    else:
        assert projection.body["client_action"] == action
        assert projection.body["retry_hint"]


def test_openapi_declares_every_chat_lifecycle_http_status():
    responses = app.openapi()["paths"]["/chat"]["post"]["responses"]
    assert {"200", "202", "409", "410", "422", "500", "503"} <= set(responses)
