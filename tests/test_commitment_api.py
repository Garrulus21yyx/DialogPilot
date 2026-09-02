from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api import main
from core.auth import Principal


def _client():
    main.app.dependency_overrides[main.get_principal] = lambda: Principal(
        subject="support-admin", scopes=frozenset({"admin"}),
    )
    return TestClient(main.app)


def _payload():
    return {
        "idempotency_key": "manual-promise-1",
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "kind": "refund_followup",
        "description": "一小时内确认退款状态",
        "due_at": datetime(2030, 1, 1, tzinfo=timezone.utc).isoformat(),
        "owner": "support-1",
        "source_kind": "manual",
    }


def test_commitment_api_create_list_transition_and_history(
    commitment_service, monkeypatch,
):
    monkeypatch.setattr(main, "_commitment_service", commitment_service)
    client = _client()

    created = client.post("/commitments", json=_payload())
    assert created.status_code == 200
    commitment_id = created.json()["commitment"]["commitment_id"]
    assert client.post("/commitments", json=_payload()).json()["created"] is False
    assert client.get("/commitments", params={"user_id": "user-1"}).json()["count"] == 1

    fulfilled = client.patch(
        f"/commitments/{commitment_id}/status",
        json={
            "status": "fulfilled", "expected_version": 1,
            "actor": "refund-worker", "receipt_ref": "refund:confirmed:1",
        },
    )
    assert fulfilled.status_code == 200
    assert fulfilled.json()["status"] == "fulfilled"
    detail = client.get(f"/commitments/{commitment_id}").json()
    assert [event["to_status"] for event in detail["events"]] == [
        "scheduled", "fulfilled",
    ]


def test_commitment_api_fails_closed_without_receipt_and_on_stale_version(
    commitment_service, monkeypatch,
):
    monkeypatch.setattr(main, "_commitment_service", commitment_service)
    client = _client()
    commitment_id = client.post(
        "/commitments", json=_payload(),
    ).json()["commitment"]["commitment_id"]

    missing_receipt = client.patch(
        f"/commitments/{commitment_id}/status",
        json={"status": "fulfilled", "expected_version": 1, "actor": "worker"},
    )
    assert missing_receipt.status_code == 422
    stale = client.patch(
        f"/commitments/{commitment_id}/status",
        json={"status": "cancelled", "expected_version": 99, "actor": "support-1"},
    )
    assert stale.status_code == 409
