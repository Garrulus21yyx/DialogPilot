"""Bounded semantic judge for tau3 RCA evidence slices."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Awaitable, Callable, Mapping


Judge = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "judgments": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "finding_code": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["SUPPORTS", "REFUTES", "UNKNOWN"]},
                    "first_bad_event_id": {"type": ["string", "null"]},
                    "owner_candidate": {"type": ["string", "null"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                    "explanation": {"type": "string", "maxLength": 2000},
                    "missing_evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                },
                "required": [
                    "finding_code", "verdict", "first_bad_event_id", "owner_candidate",
                    "evidence_ids", "explanation", "missing_evidence",
                ],
            },
        },
    },
    "required": ["judgments"],
}

SYSTEM = """You assess semantic failure hypotheses from a bounded Agent evidence slice.
Use only supplied evidence. Return UNKNOWN when evidence does not distinguish causes.
Every SUPPORTS or REFUTES judgment must cite supplied evidence IDs or observation IDs.
Do not infer tool execution, database state, authorization, or runtime success from prose.
Do not expose hidden chain-of-thought. Give a short conclusion and missing evidence.
An owner is only a candidate; your judgment cannot verify a root cause or override code,
state, action, side-effect, evaluator-availability, or causal-lineage checks.
Judge each supplied semantic hypothesis once and do not invent new task requirements."""


async def judge_report(report: Mapping[str, Any], judge: Judge) -> Mapping[str, Any]:
    judged = deepcopy(report)
    for task in judged.get("tasks", []):
        packet = evidence_packet(task)
        if not packet["hypotheses"]:
            task["llm_judge"] = {"status": "NOT_APPLICABLE", "judgments": []}
            continue
        try:
            response = await judge(packet)
            judgments = list(response.get("judgments") or [])
            _validate_citations(packet, judgments)
        except ValueError as exc:
            task["llm_judge"] = {
                "status": "INVALID", "judgments": [], "error_type": type(exc).__name__,
                "reason": str(exc)[:240],
            }
        except Exception as exc:
            task["llm_judge"] = {
                "status": "UNAVAILABLE", "judgments": [], "error_type": type(exc).__name__,
                "reason": "semantic judge did not produce a usable result",
            }
        else:
            task["llm_judge"] = {"status": "EVALUATED", "judgments": judgments}
    return judged


def evidence_packet(task: Mapping[str, Any]) -> Mapping[str, Any]:
    hypotheses = [
        {
            "finding_code": finding["code"],
            "summary": finding.get("summary"),
            "owner_candidate": finding.get("owner_candidate"),
            "evidence_ids": finding.get("evidence_ids", []),
            "missing_evidence": finding.get("missing_evidence", []),
        }
        for finding in task.get("findings", [])
        if finding.get("layer") == "hypothesis"
    ]
    evidence = [{
        "evidence_id": item["evidence_id"],
        "summary": item.get("summary"),
        "data": item.get("data", {}),
    } for item in task.get("evidence", [])]
    remote = task.get("langfuse_evidence", {})
    observations = remote.get("semantic_observations", [])
    return {
        "task_id": str(task["task_id"]),
        "outcome": task.get("outcome", {}),
        "hypotheses": hypotheses,
        "evidence": evidence,
        "causal_probes": task.get("causal_probes", {}),
        "causal_events": list(task.get("causal_events", []))[-80:],
        "semantic_observations": observations,
        "semantic_observations_truncated": remote.get("semantic_observations_truncated", 0),
    }


def structured_judge(model, *, callbacks=()):
    async def invoke(packet: Mapping[str, Any]) -> Mapping[str, Any]:
        from langchain_core.messages import HumanMessage
        from core.structured_model import structured_call
        return await structured_call(
            model, name="submit_tau3_semantic_judgments", schema=JUDGE_SCHEMA,
            system=SYSTEM,
            messages=[HumanMessage(json.dumps(packet, ensure_ascii=False))],
            callbacks=callbacks,
            metadata={"task_id": packet["task_id"], "evaluation": "tau3_semantic_rca"},
        )
    return invoke


def _validate_citations(packet: Mapping[str, Any], judgments: list[Mapping[str, Any]]) -> None:
    expected = {item["finding_code"] for item in packet["hypotheses"]}
    known = {item["evidence_id"] for item in packet["evidence"]}
    known.update(
        str(item.get("observation_id")) for item in packet["semantic_observations"]
        if item.get("observation_id")
    )
    known.update(
        str(item.get("event_id")) for item in packet["causal_events"]
        if item.get("event_id")
    )
    seen = set()
    for judgment in judgments:
        code = judgment.get("finding_code")
        if code not in expected or code in seen:
            raise ValueError("judge returned an unknown or duplicate finding_code")
        seen.add(code)
        citations = set(judgment.get("evidence_ids") or [])
        if not citations.issubset(known):
            raise ValueError("judge cited evidence outside the supplied packet")
        if judgment.get("verdict") != "UNKNOWN" and not citations:
            raise ValueError("non-UNKNOWN judgment requires evidence citations")
        first_bad_event_id = judgment.get("first_bad_event_id")
        if first_bad_event_id is not None and str(first_bad_event_id) not in known:
            raise ValueError("judge cited first_bad_event_id outside the supplied packet")
    if seen != expected:
        raise ValueError("judge omitted a semantic hypothesis")
