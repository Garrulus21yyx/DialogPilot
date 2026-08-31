"""回答选择、客户端 ACK 与断线续取的持久合同测试。"""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from api import main
from core.auth import Principal
from services.response_delivery import (
    DeliveryStatus,
    ResponseDeliveryService,
    ResponseNotFoundError,
)


def test_conversation_sequence_is_unique_contiguous_under_concurrency(tmp_path):
    service = ResponseDeliveryService(str(tmp_path / "responses.db"))

    def select(index):
        return service.select_response(
            user_id="user-1", conv_id="conversation-1", request_id=f"request-{index}",
            response_text=f"answer-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        deliveries = list(executor.map(select, range(20)))

    assert sorted(item.seq for item in deliveries) == list(range(1, 21))
    assert [item.seq for item in service.list_after(
        user_id="user-1", conv_id="conversation-1", after_seq=15,
    )] == [16, 17, 18, 19, 20]


def test_ack_state_is_monotonic_idempotent_and_scoped_to_authenticated_user(tmp_path):
    service = ResponseDeliveryService(str(tmp_path / "responses.db"))
    selected = service.select_response(
        user_id="user-1", conv_id="conversation-1", request_id="request-1",
        response_text="selected answer",
    )

    delivered = service.acknowledge(
        selected.response_id, user_id="user-1", status=DeliveryStatus.DELIVERED,
    )
    read = service.acknowledge(
        selected.response_id, user_id="user-1", status=DeliveryStatus.READ,
    )
    late = service.acknowledge(
        selected.response_id, user_id="user-1", status=DeliveryStatus.DELIVERED,
    )

    assert delivered.status is DeliveryStatus.DELIVERED
    assert delivered.delivered_at is not None
    assert read.status is DeliveryStatus.READ
    assert read.read_at is not None
    assert late.status is DeliveryStatus.READ
    assert late.read_at == read.read_at
    try:
        service.acknowledge(
            selected.response_id, user_id="user-2", status=DeliveryStatus.READ,
        )
    except ResponseNotFoundError:
        pass
    else:
        raise AssertionError("cross-user ACK must fail as not found")


def test_read_ack_from_selected_records_delivery_and_read_together(tmp_path):
    service = ResponseDeliveryService(str(tmp_path / "responses.db"))
    selected = service.select_response(
        user_id="user-1", conv_id="conversation-1", request_id="request-1",
        response_text="answer",
    )
    read = service.acknowledge(
        selected.response_id, user_id="user-1", status=DeliveryStatus.READ,
    )

    assert read.status is DeliveryStatus.READ
    assert read.delivered_at is not None
    assert read.read_at is not None


def test_delivery_cursor_and_ack_survive_service_restart(tmp_path):
    path = tmp_path / "responses.db"
    first_service = ResponseDeliveryService(str(path))
    selected = first_service.select_response(
        user_id="user-1", conv_id="conversation-1", request_id="request-1",
        response_text="recoverable answer",
    )

    restarted = ResponseDeliveryService(str(path))
    restored = restarted.list_after(
        user_id="user-1", conv_id="conversation-1", after_seq=0,
    )[0]
    acknowledged = restarted.acknowledge(
        selected.response_id, user_id="user-1", status=DeliveryStatus.DELIVERED,
    )

    assert restored.response_id == selected.response_id
    assert restored.response_text == "recoverable answer"
    assert acknowledged.status is DeliveryStatus.DELIVERED


def test_ack_and_replay_http_contract(tmp_path, monkeypatch):
    service = ResponseDeliveryService(str(tmp_path / "responses.db"))
    first = service.select_response(
        user_id="user-1", conv_id="conversation-1", request_id="request-1",
        response_text="first",
    )
    second = service.select_response(
        user_id="user-1", conv_id="conversation-1", request_id="request-2",
        response_text="second",
    )
    monkeypatch.setattr(main, "_response_delivery", service)
    main.app.dependency_overrides[main.get_principal] = lambda: Principal(
        subject="user-1", scopes=frozenset({"chat"}),
    )
    try:
        client = TestClient(main.app)
        replay = client.get("/conversations/conversation-1/responses", params={"after_seq": 1})
        assert replay.status_code == 200
        assert replay.json()["responses"][0]["response_id"] == second.response_id
        assert replay.json()["last_seq"] == 2

        ack = client.post(f"/responses/{first.response_id}/ack", json={"status": "read"})
        assert ack.status_code == 200
        assert ack.json()["status"] == "read"
        assert client.post("/responses/missing/ack", json={"status": "delivered"}).status_code == 404
        assert client.post(f"/responses/{first.response_id}/ack", json={"status": "selected"}).status_code == 422
    finally:
        main.app.dependency_overrides.clear()
