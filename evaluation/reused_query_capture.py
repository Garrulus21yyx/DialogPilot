"""Validation boundary for a previously generated query-transform artifact."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.model_policy import ModelPolicy, ModelRole
from evaluation.rag_query_capture import select_query_cases
from evaluation.rag_pipeline.dataset import RagDataset
from mcp.query_transformer import QUERY_TRANSFORM_PROMPT_VERSION


@dataclass(frozen=True)
class ReusedStandaloneCapture:
    path: Path
    sha256: str
    dataset_id: str
    split: str
    sample_policy: str
    prompt_version: str
    captured_model_policy: Mapping[str, Any]
    original_usage: Mapping[str, int]
    rows: tuple[Mapping[str, Any], ...]


def load_reused_standalone_capture(
    path: Path | str,
    *,
    dataset: RagDataset,
    model_policy: ModelPolicy,
) -> ReusedStandaloneCapture:
    """Bind prior Dev output to its dataset and current production profile."""
    artifact_path = Path(path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schema_version", -1)) != 1:
        raise ValueError("unsupported query-transform capture schema")
    if str(payload.get("dataset_id") or "") != str(dataset.manifest["dataset_id"]):
        raise ValueError("query-transform capture dataset mismatch")
    if payload.get("split") != "dev":
        raise ValueError("query-transform capture must be from Dev")
    if payload.get("prompt_version") != QUERY_TRANSFORM_PROMPT_VERSION:
        raise ValueError("query-transform prompt version drift")
    _validate_model_identity(payload.get("model_policy"), model_policy)

    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("query-transform capture rows are required")
    if int(payload.get("case_count", -1)) != len(raw_rows):
        raise ValueError("query-transform capture case count mismatch")
    rows = _validate_rows(dataset, raw_rows)
    expected_case_ids = {
        case.case_id
        for case in select_query_cases(
            dataset,
            split="dev",
            max_cases=len(rows),
        )
    }
    if {str(row["case_id"]) for row in rows} != expected_case_ids:
        raise ValueError("query-transform capture cohort selection drift")
    actual_error_cases = sum(bool(row.get("errors")) for row in rows)
    if int(payload.get("error_case_count", -1)) != actual_error_cases:
        raise ValueError("query-transform capture error count mismatch")

    return ReusedStandaloneCapture(
        path=artifact_path,
        sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        dataset_id=str(payload["dataset_id"]),
        split="dev",
        sample_policy=str(payload.get("sample_policy") or ""),
        prompt_version=QUERY_TRANSFORM_PROMPT_VERSION,
        captured_model_policy=dict(payload["model_policy"]),
        original_usage={
            key: int(payload.get(key, 0))
            for key in (
                "total_calls",
                "total_input_tokens",
                "total_output_tokens",
                "error_case_count",
            )
        },
        rows=rows,
    )


def rewrite_status(captured: Mapping[str, Any]) -> str:
    errors = tuple(
        str(error)
        for error in captured.get("errors") or ()
        if str(error).startswith("standalone:")
    )
    standalone = str(captured["standalone"])
    raw = str(captured["raw_query"])
    if errors:
        return "FALLBACK_ERROR"
    if not standalone.strip() or standalone == raw:
        return "FALLBACK_IDENTICAL"
    return "REWRITTEN"


def _validate_model_identity(captured: Any, current: ModelPolicy) -> None:
    if not isinstance(captured, dict):
        raise ValueError("query-transform model policy is required")
    profile = captured.get("rewrite")
    if not isinstance(profile, dict):
        raise ValueError("query-transform rewrite profile is required")
    expected = current.profile(ModelRole.REWRITE)
    pairs = {
        "provider": (captured.get("provider"), current.provider),
        "base_url": (
            str(captured.get("base_url") or "official").rstrip("/"),
            str(current.base_url or "official").rstrip("/"),
        ),
        "model": (profile.get("model"), expected.model),
        "reasoning": (profile.get("reasoning"), expected.reasoning.value),
        "min_completion_tokens": (
            profile.get("min_completion_tokens"),
            expected.min_completion_tokens,
        ),
    }
    drift = [name for name, (actual, wanted) in pairs.items() if actual != wanted]
    if drift:
        raise ValueError("query-transform provider/profile drift: " + ", ".join(drift))


def _validate_rows(
    dataset: RagDataset,
    raw_rows: list[Any],
) -> tuple[Mapping[str, Any], ...]:
    dev_cases = {case.case_id: case for case in dataset.select_cases("dev")}
    rows: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for value in raw_rows:
        if not isinstance(value, dict):
            raise ValueError("query-transform row must be an object")
        case_id = str(value.get("case_id") or "")
        case = dev_cases.get(case_id)
        if case is None or case_id in seen:
            raise ValueError(f"invalid query-transform Dev case: {case_id}")
        seen.add(case_id)
        if (
            str(value.get("group_id") or "") != case.group_id
            or str(value.get("raw_query") or "") != case.query
            or int(value.get("history_turns", -1)) != len(case.history)
        ):
            raise ValueError(f"query-transform row binding drift: {case_id}")
        if not isinstance(value.get("standalone"), str):
            raise ValueError(f"standalone query must be text: {case_id}")
        errors = value.get("errors")
        if not isinstance(errors, list) or not all(
            isinstance(error, str) for error in errors
        ):
            raise ValueError(f"query-transform errors must be text: {case_id}")
        rows.append(value)
    return tuple(sorted(rows, key=lambda row: str(row["case_id"])))
