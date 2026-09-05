"""Locked L0 clarification selection, scoring, and artifact output."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from application.chat_contracts import Completed


@dataclass(frozen=True)
class LiveProviderIdentity:
    provider: str
    model: str
    model_profile_fingerprint: str
    sdk_version: str
    real_provider: bool


def load_locked_l0_clarification_cases(
    dataset_root: str | Path,
    *,
    case_ids: Sequence[str] = (),
) -> tuple[dict[str, Any], ...]:
    root = Path(dataset_root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    raw = (root / "cases.jsonl").read_bytes()
    if manifest.get("lock_status") != "SYNTHETIC_CONTRACT_LOCKED" or manifest.get(
        "cases_sha256"
    ) != _sha256(raw):
        raise ValueError("synthetic contract lock is invalid")
    wanted = set(case_ids)
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    selected = tuple(row for row in rows if _is_l0_clarification(row))
    if wanted:
        selected = tuple(row for row in selected if row["case_id"] in wanted)
        if {row["case_id"] for row in selected} != wanted:
            raise ValueError(
                "requested case is outside the locked L0 clarification slice"
            )
    if not selected:
        raise ValueError("locked L0 clarification slice is empty")
    return selected


def score_locked_l0_turn(
    *,
    case: Mapping[str, Any],
    outcome: Any,
    chat_plan: Any,
    completion_call: Mapping[str, Any] | None,
    usage: Mapping[str, Any],
    provider: LiveProviderIdentity,
    latency_ms: float,
    run_id: str,
) -> dict[str, Any]:
    expected = case["turns"][0]["expected"]
    response = outcome.response if isinstance(outcome, Completed) else {}
    stages = {item.stage: item for item in getattr(outcome, "stages", ())}
    plan = chat_plan.plan if chat_plan is not None else None
    actual = {
        "outcome_type": type(outcome).__name__,
        "understanding_status": (
            chat_plan.planning.understanding.status.value if chat_plan else None
        ),
        "semantic_router_used": (
            chat_plan.planning.semantic_router_used if chat_plan else None
        ),
        "route_mode": plan.route.mode.value if plan else None,
        "command_primary": bool(chat_plan and chat_plan.use_primary),
        "routing_disposition": response.get("routing_disposition"),
        "legacy_intent_status": (
            stages["intent"].status.value if "intent" in stages else None
        ),
        "knowledge_used": bool(response.get("knowledge_used")),
        "ocr_invoked": bool((response.get("media") or {}).get("ocr_invoked")),
        "vlm_invoked": bool((response.get("media") or {}).get("vlm_invoked")),
        "tool_call_count": len(response.get("tool_audit") or ()),
        "missing_inputs": list(
            (response.get("coverage") or {}).get("missing_inputs", ())
        ),
    }
    checks = {
        "provider_output_consumed": bool(
            completion_call and completion_call.get("output_sha256")
        ),
        "clarification_terminal": (
            actual["outcome_type"] == "Completed"
            and actual["understanding_status"] == "CLARIFY"
            and actual["routing_disposition"] == "clarify"
            and bool(actual["missing_inputs"])
        ),
        "route_oracle": actual["route_mode"] == str(expected["route_mode"]).lower(),
        "command_primary_owner": (
            actual["command_primary"] and actual["legacy_intent_status"] == "skipped"
        ),
        "knowledge_oracle": (
            not actual["knowledge_used"]
            if expected["knowledge"]["must_not_retrieve"]
            else True
        ),
        "media_oracle": (
            not actual["vlm_invoked"]
            if expected["media"]["must_not_call_vlm"]
            else True
        )
        and not actual["ocr_invoked"],
        "tool_oracle": (
            actual["tool_call_count"] == 0
            if not expected["tools"]["expected_calls"]
            else True
        ),
    }
    total = usage["total"]
    usage_calls = tuple(usage["calls"])
    eligible = (
        provider.real_provider
        and all(
            (
                provider.provider,
                provider.model,
                provider.sdk_version,
                provider.model_profile_fingerprint,
            )
        )
        and completion_call is not None
        and completion_call.get("error_type") is None
        and completion_call.get("output_sha256") is not None
        and int(total["calls"]) == 1
        and int(total["errors"]) == 0
        and all(item["model"] == provider.model for item in usage_calls)
    )
    provider_failure = (
        actual["understanding_status"] == "PROVIDER_FAILURE" or int(total["errors"]) > 0
    )
    return {
        "schema_version": 1,
        "run_id": run_id,
        "case_id": case["case_id"],
        "turn_id": case["turns"][0]["turn_id"],
        "status": (
            "PROVIDER_FAILURE"
            if provider_failure
            else "PASS"
            if all(checks.values())
            else "FAIL"
        ),
        "score_eligible": eligible,
        "checks": checks,
        "actual": actual,
        "model_output_sha256": (
            completion_call.get("output_sha256") if completion_call else None
        ),
        "usage": {
            key: total[key]
            for key in ("calls", "errors", "input_tokens", "output_tokens")
        },
        "latency_ms": latency_ms,
    }


def _is_l0_clarification(case: Mapping[str, Any]) -> bool:
    turns = case.get("turns") or ()
    if case.get("status") != "SYNTHETIC_CONTRACT_LOCKED" or len(turns) != 1:
        return False
    turn = turns[0]
    return (
        not turn.get("attachment_refs")
        and turn["expected"]["media"]["media_need"] == "L0"
        and turn["expected"]["route_mode"] == "CLARIFY"
        and case["expected_outcome"]["terminal_kind"] == "CLARIFY"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
