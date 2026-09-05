import asyncio
import subprocess
import sys

import httpx
import pytest

from api.cli import display_response, run_cli
from api.chat_client import submit_turn


@pytest.mark.parametrize("terminal", [
    {"final_response": {"response": "查询完成"}},
    {"pending_signal": {"challenge": "请补充订单号"}},
    {"runtime": {"execution_status": "FAILED"}},
])
def test_cli_submits_once_then_reads_the_original_invocation(terminal):
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={
                "outcome": "accepted", "public_status": {"invocation_key": "inv-1"},
            })
        assert request.url.path == "/invocations/inv-1"
        return httpx.Response(200, json=(
            {"runtime": {"execution_status": "RUNNING"}} if len(requests) == 2 else terminal
        ))

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://test",
        ) as client:
            return await submit_turn(
                client, "查询订单", conversation_id="c1", request_id="r1", poll_seconds=0,
            )

    assert asyncio.run(run()) == terminal
    assert [request.method for request in requests] == ["POST", "GET", "GET"]


def test_cli_wait_timeout_does_not_cancel_or_resubmit():
    requests = []
    accepted = {"outcome": "accepted", "public_status": {"invocation_key": "inv-1"}}

    def handle(request):
        requests.append(request)
        return httpx.Response(202, json=accepted)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="http://test",
        ) as client:
            return await submit_turn(
                client, "查询订单", conversation_id="c1", request_id="r1", wait_seconds=0,
            )

    assert asyncio.run(run()) == accepted
    assert len(requests) == 1


def test_cli_requires_authentication(monkeypatch):
    monkeypatch.delenv("DIALOGPILOT_AUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="DIALOGPILOT_AUTH_TOKEN"):
        asyncio.run(run_cli())


def test_cli_and_transport_evaluation_do_not_import_legacy_engines():
    subprocess.run([sys.executable, "-c", """
import sys
import api.cli
import evaluation.chat_application_runner
import evaluation.command_primary_eval.locked_conversation
import evaluation.evaluator
import api.main
assert not ({'agents.agent_orchestrator', 'agents.react_engine', 'agents.run_store',
             'application.chat_application'} & sys.modules.keys())
"""], check=True)


def test_cli_renders_committed_answer_and_pending_question():
    assert display_response({"final_response": {"response": "已查询"}}) == "已查询"
    assert display_response({"pending_signal": {"challenge": "请提供订单号"}}) == "请提供订单号"
