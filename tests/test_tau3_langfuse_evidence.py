"""Bounded, session-isolated Observations v2 evidence reads."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from langfuse.api.client import LangfuseAPI

from evaluation.tau3_langfuse_evidence import LangfuseEvidenceError, enrich_from_langfuse


START = "2026-09-09T02:30:00Z"
END = "2026-09-09T03:00:00Z"


def report():
    return {"run": {"started_at": START, "finished_at": END}, "tasks": [
        {"task_id": "8", "langfuse_session_id": "session-8"},
    ]}


def row(observation_id="obs-1", trace_id="trace-1", session_id="session-8"):
    return {"id": observation_id, "traceId": trace_id, "sessionId": session_id,
            "projectId": "project", "type": "GENERATION", "startTime": START,
            "usageDetails": {"input": 10, "output": 2, "total": 12}}


def page(rows, cursor=None):
    return {"data": rows, "meta": {"cursor": cursor}}


class Client:
    # No legacy trace API exists on this fake: any old call fails the test.
    def __init__(self, pages):
        self.pages = iter(pages)
        self.requests = []
        self.api = SimpleNamespace(observations=self)

    def get_many(self, **kwargs):
        self.requests.append(kwargs)
        response = next(self.pages)
        if isinstance(response, Exception):
            raise response
        return response


def test_cursor_pages_deduplicate_observations_and_trace_ids():
    first = row()
    client = Client([page([first], "one"), page([], "two"),
                     page([first, row("obs-2"), row("obs-1", "trace-2")])])
    original = report()
    before = deepcopy(original)
    evidence = enrich_from_langfuse(original, client)["tasks"][0]["langfuse_evidence"]
    assert original == before
    assert evidence["status"] == "FETCHED"
    assert evidence["trace_ids"] == ["trace-1", "trace-2"]
    assert evidence["observation_count"] == 3
    assert evidence["token_usage"]["total_tokens"] == 36
    assert [request.get("cursor") for request in client.requests] == [None, "one", "two"]
    for request in client.requests:
        assert request["session_id"] == "session-8"
        assert request["from_start_time"] == datetime(2026, 9, 9, 2, 30, tzinfo=timezone.utc)
        assert request["to_start_time"] == datetime(2026, 9, 9, 3, tzinfo=timezone.utc)
        assert "filter" not in request  # Cannot shadow the time query parameters.
        assert "parse_io_as_json" not in request
        assert {"basic", "io", "metadata", "usage"} <= set(request["fields"].split(","))


def test_pinned_sdk_serializes_v2_session_and_bounds_and_decodes_models():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=page([row()], "next") if len(requests) == 1 else page([]))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        api = LangfuseAPI(base_url="https://langfuse.test", username="test", password="test", httpx_client=http)
        result = enrich_from_langfuse(report(), SimpleNamespace(api=api))
    assert result["tasks"][0]["langfuse_evidence"]["trace_ids"] == ["trace-1"]
    assert len(requests) == 2
    for request in requests:
        assert request.url.path == "/api/public/v2/observations"
        assert request.url.params["sessionId"] == "session-8"
        assert datetime.fromisoformat(request.url.params["fromStartTime"]) == datetime.fromisoformat(START)
        assert datetime.fromisoformat(request.url.params["toStartTime"]) == datetime.fromisoformat(END)
    assert requests[1].url.params["cursor"] == "next"


@pytest.mark.parametrize("bad", [None, "", "yesterday", "2026-09-09T02:00:00", 123])
def test_invalid_or_missing_bounds_fail_before_request(bad):
    original = report()
    original["run"]["started_at"] = bad
    client = Client([])
    with pytest.raises(LangfuseEvidenceError, match="--from-start-time"):
        enrich_from_langfuse(original, client)
    assert client.requests == []


@pytest.mark.parametrize("end", [START, "2026-09-08T00:00:00Z"])
def test_invalid_interval_fails_before_request(end):
    client = Client([])
    with pytest.raises(LangfuseEvidenceError, match="precede"):
        enrich_from_langfuse(report(), client, to_start_time=end)
    assert client.requests == []


def test_explicit_bounds_override_missing_run_metadata_and_normalize_timezone():
    original = report()
    original.pop("run")
    client = Client([page([])])
    result = enrich_from_langfuse(original, client, from_start_time="2020-01-01T01:00:00+01:00",
                                 to_start_time=datetime(2020, 1, 2, tzinfo=timezone.utc))
    evidence = result["tasks"][0]["langfuse_evidence"]
    assert evidence["from_start_time"] == "2020-01-01T00:00:00+00:00"
    assert evidence["observation_count"] == 0
    assert evidence["trace_ids"] == []
    assert evidence["token_usage"]["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("response", [None, {}, {"data": []}, {"data": None, "meta": {}},
                                      {"data": {}, "meta": {}}, {"data": [], "meta": []},
                                      {"data": [], "meta": "bad"}, page([row(session_id="other")]),
                                      page([row(session_id=None)]), page([row(trace_id=None)]),
                                      page([row(observation_id=None)]), page([], 42)])
def test_invalid_responses_never_return_fetched(response):
    with pytest.raises(LangfuseEvidenceError):
        enrich_from_langfuse(report(), Client([response]))


@pytest.mark.parametrize("cursors", [["a", "a"], ["a", "b", "a"]])
def test_cursor_cycles_fail_without_mutating_input(cursors):
    original = report()
    before = deepcopy(original)
    with pytest.raises(LangfuseEvidenceError, match="cursor"):
        enrich_from_langfuse(original, Client([page([row()], cursor) for cursor in cursors]))
    assert original == before


def test_later_api_failure_preserves_exception_type_and_input():
    class ApiFailure(Exception):
        pass
    original = report()
    before = deepcopy(original)
    error = ApiFailure("rate limited")
    with pytest.raises(ApiFailure) as caught:
        enrich_from_langfuse(original, Client([page([row()], "next"), error]))
    assert caught.value is error
    assert original == before


def test_each_task_uses_its_own_session_filter():
    original = report()
    original["tasks"].append({"task_id": "9", "langfuse_session_id": "session-9"})
    client = Client([page([row()]), page([row("obs-2", "trace-2", "session-9")])])
    result = enrich_from_langfuse(original, client)
    assert [request["session_id"] for request in client.requests] == ["session-8", "session-9"]
    assert [task["langfuse_evidence"]["trace_ids"] for task in result["tasks"]] == [["trace-1"], ["trace-2"]]


def test_missing_session_remains_unavailable_without_requiring_bounds():
    client = Client([])
    result = enrich_from_langfuse({"tasks": [{"task_id": "8"}]}, client)
    assert result["tasks"][0]["langfuse_evidence"]["status"] == "UNAVAILABLE"
    assert client.requests == []


@pytest.mark.parametrize("command", ["enrich-langfuse", "inspect"])
def test_cli_passes_explicit_time_bounds(command, tmp_path, monkeypatch):
    import json
    import sys
    from scripts import tau3_eval_loop
    original = report()
    original["run"]["path"] = str(tmp_path)
    input_path = tmp_path / "report.json"
    input_path.write_text(json.dumps(original))
    output_path = tmp_path / "output.json"
    monkeypatch.setattr(tau3_eval_loop, "_load_env", lambda path: {})
    monkeypatch.setattr(tau3_eval_loop, "analyze_run", lambda path: original)
    monkeypatch.setattr(tau3_eval_loop, "probe_report", lambda value, path: value)
    calls = []
    def enrich(value, **kwargs):
        calls.append(kwargs)
        return value
    monkeypatch.setattr(tau3_eval_loop, "enrich_from_langfuse", enrich)
    monkeypatch.setattr(sys, "argv", ["tau3_eval_loop", command, str(input_path),
                                     "--from-start-time", START, "--to-start-time", END,
                                     "--output", str(output_path)])
    with pytest.raises(SystemExit) as caught:
        tau3_eval_loop.main()
    assert caught.value.code == 0
    assert calls == [{"from_start_time": START, "to_start_time": END}]
    assert json.loads(output_path.read_text())["tasks"] == original["tasks"]


def test_inspect_keeps_local_analysis_unavailable_after_fetch_failure(tmp_path, monkeypatch):
    import json
    import sys
    from scripts import tau3_eval_loop
    original = report()
    before = deepcopy(original)
    client = Client([page([row()], "again"), page([], "again")])
    monkeypatch.setattr(tau3_eval_loop, "_load_env", lambda path: {})
    monkeypatch.setattr(tau3_eval_loop, "analyze_run", lambda path: deepcopy(original))
    monkeypatch.setattr(tau3_eval_loop, "probe_report", lambda value, path: value)
    monkeypatch.setattr(tau3_eval_loop, "enrich_from_langfuse",
                        lambda value, **kwargs: enrich_from_langfuse(value, client, **kwargs))
    output = tmp_path / "output.json"
    monkeypatch.setattr(sys, "argv", ["tau3_eval_loop", "inspect", str(tmp_path), "--output", str(output)])
    with pytest.raises(SystemExit):
        tau3_eval_loop.main()
    result = json.loads(output.read_text())
    assert result["observability_status"] == "UNAVAILABLE"
    assert result["observability_error"] == {"error_type": "LangfuseEvidenceError"}
    assert result["tasks"] == before["tasks"]


def test_enrich_command_does_not_write_partial_output(tmp_path, monkeypatch):
    import json
    import sys
    from scripts import tau3_eval_loop
    input_path = tmp_path / "report.json"
    input_path.write_text(json.dumps(report()))
    output = tmp_path / "output.json"
    client = Client([page([row()], "next"), RuntimeError("fetch failed")])
    monkeypatch.setattr(tau3_eval_loop, "_load_env", lambda path: {})
    monkeypatch.setattr(tau3_eval_loop, "enrich_from_langfuse",
                        lambda value, **kwargs: enrich_from_langfuse(value, client, **kwargs))
    monkeypatch.setattr(sys, "argv", ["tau3_eval_loop", "enrich-langfuse", str(input_path), "--output", str(output)])
    with pytest.raises(RuntimeError, match="fetch failed"):
        tau3_eval_loop.main()
    assert not output.exists()
