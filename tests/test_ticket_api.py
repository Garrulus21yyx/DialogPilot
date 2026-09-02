from fastapi.testclient import TestClient

from api import main
from core.auth import Principal


def payload():
    return {
        "idempotency_key": "manual-request-1",
        "user_id": "user-1",
        "conv_id": "conversation-1",
        "request_id": "manual-request-1",
        "question": "I need a human",
        "published_response": "handoff requested",
        "reason": "user requested a human",
        "priority": "high",
        "agent_type": "general",
        "intent": "human_handoff",
        "verification_status": "pass",
    }


def admin_client():
    """工单 HTTP 是管理面；测试显式注入已认证 admin Principal。"""
    main.app.dependency_overrides[main.get_principal] = lambda: Principal(
        subject="support-admin",
        scopes=frozenset({"admin"}),
    )
    return TestClient(main.app)


def test_ticket_api_create_list_detail_and_transition(ticket_service, monkeypatch):
    """证明创建、列表、详情和合法迁移的完整 API 主路径。"""
    service = ticket_service
    monkeypatch.setattr(main, "_ticket_service", service)
    client = admin_client()

    created = client.post("/tickets", json=payload())
    assert created.status_code == 200
    assert created.json()["created"] is True
    ticket_id = created.json()["ticket"]["ticket_id"]

    retry = client.post("/tickets", json=payload())
    assert retry.status_code == 200
    assert retry.json()["created"] is False
    assert retry.json()["ticket"]["ticket_id"] == ticket_id

    listed = client.get("/tickets", params={"user_id": "user-1", "status": "open"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert client.get("/tickets", params={"limit": 0}).status_code == 422
    assert client.get("/tickets", params={"limit": 201}).status_code == 422

    transitioned = client.patch(
        f"/tickets/{ticket_id}/status",
        json={"status": "in_progress", "actor": "support-1", "note": "claimed"},
    )
    assert transitioned.status_code == 200
    assert transitioned.json()["status"] == "in_progress"
    assert transitioned.json()["assignee"] == "support-1"

    detail = client.get(f"/tickets/{ticket_id}")
    assert detail.status_code == 200
    assert [event["to_status"] for event in detail.json()["events"]] == [
        "open",
        "in_progress",
    ]


def test_ticket_api_maps_conflict_and_missing_states(ticket_service, monkeypatch):
    """证明幂等冲突与缺失工单映射为稳定 HTTP 错误。"""
    service = ticket_service
    monkeypatch.setattr(main, "_ticket_service", service)
    client = admin_client()

    created = client.post("/tickets", json=payload()).json()
    ticket_id = created["ticket"]["ticket_id"]

    conflicting = payload()
    conflicting["question"] = "different operation with reused key"
    assert client.post("/tickets", json=conflicting).status_code == 409

    illegal = client.patch(
        f"/tickets/{ticket_id}/status",
        json={"status": "resolved", "actor": "support-1"},
    )
    assert illegal.status_code == 409
    assert illegal.json()["detail"]["error"] == "invalid_ticket_transition"

    assert client.get("/tickets/missing").status_code == 404
"""工单 HTTP 投影及领域异常映射测试。"""
