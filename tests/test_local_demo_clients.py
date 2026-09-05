"""Demo acceptance consumes the same durable HTTP contract as the CLI."""
import asyncio
import json

import httpx
import pytest

from scripts import run_local_e2e as ocr_demo
from scripts import run_local_vlm_e2e as visual_demo


@pytest.mark.parametrize("demo", [ocr_demo, visual_demo])
@pytest.mark.parametrize("delivery", ["immediate", "delayed", "failed", "waiting"])
@pytest.mark.parametrize("evidence_valid", [True, False])
def test_demo_observes_original_turn_and_requires_consumed_evidence(
    monkeypatch, demo, delivery, evidence_valid,
):
    requests = []
    visual = demo is visual_demo
    real_client = httpx.AsyncClient
    monkeypatch.setattr(demo, "_token", lambda: "test-token")
    monkeypatch.setattr(demo, "_demo_image" if visual else "_demo_png", lambda: b"image")

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/knowledge/stats":
            return httpx.Response(200, json={
                "storage_backend": {"engine": "postgresql+pgvector+pg_fts"},
                "total_chunks": 1,
            })
        if request.url.path == "/assets/upload":
            return httpx.Response(201, json={
                "asset_id": "asset-1", "status": "SCANNED",
                "ocr_invoked": False, "vlm_invoked": False,
            })
        if request.url.path == "/chat":
            body = json.loads(request.content)
            assert body["asset_ids"] == ["asset-1"]
            if delivery == "immediate":
                return httpx.Response(200, json=completed(body["request_id"]))
            return httpx.Response(202, json={
                "outcome": "accepted", "public_status": {"invocation_key": "inv-1"},
            })
        assert request.url.path == "/invocations/inv-1"
        assert request.method == "GET"
        if delivery == "failed":
            return httpx.Response(200, json={"runtime": {"execution_status": "FAILED"}})
        if delivery == "waiting":
            return httpx.Response(200, json={"pending_signal": {"challenge": "Need input"}})
        posted = next(item for item in requests if item.url.path == "/chat")
        return httpx.Response(200, json={
            "final_response": completed(json.loads(posted.content)["request_id"]),
        })

    def completed(request_id):
        return {
            "request_id": request_id, "response": "E42；红框里的按钮不可用。",
            "verified": True, "agent_outcomes": [{"status": "SUCCEEDED"}],
            "evaluation_trace": {"consumption": {"facts": [{
                "requirement_id": "media.visual_observation" if visual else "media.visible_text",
                "source_kind": "MEDIA_OBSERVED" if evidence_valid else "USER_ASSERTED",
                "producer_id": "media_observe" if visual else "media_read",
                "producer_version": "test-v1", "source_ref": "call-1",
            }]}},
        }

    monkeypatch.setattr(demo.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    report = asyncio.run(demo.run("http://test") if visual else demo.run("http://test", "read"))
    assert report["verification"] == (
        "PASS" if delivery in {"immediate", "delayed"} and evidence_valid else "FAIL"
    )
    assert len([item for item in requests if item.url.path == "/chat"]) == 1
    assert len([item for item in requests if item.url.path == "/assets/upload"]) == 1
    assert not any(item.method in {"DELETE", "PATCH"} for item in requests)
