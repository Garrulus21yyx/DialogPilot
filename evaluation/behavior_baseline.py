"""Replayable M0 behavior/version baseline without production-accuracy claims."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class BehaviorBaselineError(ValueError):
    pass


DECISION_POLICY_BASELINE_V1: Mapping[str, Any] = {
    "intent_fusion": {
        "ngram_weights": {"llm": 0.70, "embedding": 0.20, "pattern": 0.10},
        "disabled_weights": {"llm": 0.85, "pattern": 0.15},
        "accept_threshold": 0.50,
    },
    "domain_routing": {
        "priors": {"general": 0.10},
        "intent_scores": {
            "general": 0.55,
            "technical": 0.75,
            "billing": 0.75,
            "account_security": 0.85,
        },
        "keyword_scores": {
            "technical": {"first": 0.45, "additional": 0.10, "cap": 0.65},
            "billing": {"first": 0.45, "additional": 0.10, "cap": 0.65},
            "account_security": {"first": 0.55, "additional": 0.10, "cap": 0.75},
            "general": {"each": 0.12, "cap": 0.35},
        },
        "entity_scores": {"error_code": 0.20, "amount": 0.15, "order_id": 0.10},
        "supporting_threshold": 0.45,
        "clarification_threshold": 0.50,
    },
    "execution": {
        "legacy_planner_domain_task_cap": 4,
        "request_timeout_seconds": 20.0,
        "worker_timeout_seconds": 15.0,
        "executed_task_cap": 3,
        "react_step_cap": 4,
    },
    "instance_selection": {
        "score_weights": {"success": 0.35, "quality": 0.45, "latency": 0.20},
        "quality_ewma_alpha": 0.25,
        "quality_prior": 0.50,
        "quality_initial": 0.50,
        "full_confidence_samples": 10,
        "latency_score": "1/(1+avg_ms/1000)",
        "success_penalty": "min(0.50,(0.90-success_rate)*2)",
        "latency_penalty": "min(0.40,(avg_ms-3000)/10000)",
        "total_penalty_cap": 0.90,
        "multi_instance_status": "NOT_APPLICABLE",
    },
    "knowledge_retrieval": {
        "raw_query_weight": 0.25,
        "standalone_query_weight": 0.75,
        "dense_weight": 0.25,
        "bm25_weight": 0.75,
        "rrf_k": 10,
        "candidate_k": 20,
        "top_k": 5,
        "context_max_tokens": 2600,
    },
    "memory_retrieval": {
        "vector_weight": 0.30,
        "bm25_weight": 0.60,
        "recency_weight": 0.10,
        "rrf_k": 60,
        "lexical_pool": 20,
    },
    "active_case_context": {
        "excluded_statuses": ["closed"],
        "limit": 3,
        "order": ["updated_at DESC", "created_at DESC", "ticket_id ASC"],
        "section_priority": 90,
    },
}


def policy_fingerprints(
    policies: Mapping[str, Any] = DECISION_POLICY_BASELINE_V1,
) -> dict[str, str]:
    return {
        name: _sha256_json(value)
        for name, value in sorted(policies.items())
    }


@dataclass(frozen=True)
class BehaviorRunRecord:
    case_id: str
    request_id: str
    trace_id: str
    route: str
    latency_ms: float
    model_calls: int
    tool_calls: int
    publication_status: str
    failure_type: str
    stages: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BehaviorRunRecord":
        record = cls(
            case_id=_required(raw.get("case_id"), "case_id"),
            request_id=_required(raw.get("request_id"), "request_id"),
            trace_id=_required(raw.get("trace_id"), "trace_id"),
            route=_required(raw.get("route"), "route"),
            latency_ms=float(raw.get("latency_ms", -1)),
            model_calls=int(raw.get("model_calls", -1)),
            tool_calls=int(raw.get("tool_calls", -1)),
            publication_status=_required(
                raw.get("publication_status"), "publication_status",
            ),
            failure_type=str(raw.get("failure_type") or "none"),
            stages=tuple(dict(item) for item in (raw.get("stages") or [])),
        )
        if not math.isfinite(record.latency_ms) or record.latency_ms < 0:
            raise BehaviorBaselineError("latency_ms must be finite and non-negative")
        if record.model_calls < 0 or record.tool_calls < 0:
            raise BehaviorBaselineError("call counts must be non-negative")
        if not record.stages:
            raise BehaviorBaselineError("stage observations are required")
        return record

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["stages"] = [dict(item) for item in self.stages]
        return value


def build_behavior_baseline(
    *,
    baseline_id: str,
    created_at: str,
    commit_sha: str,
    tree_sha: str,
    bundle: Mapping[str, Any],
    model_policy: Mapping[str, Any],
    rag_index_manifest: Mapping[str, Any],
    environment_observations: Mapping[str, Any] | None = None,
    datasets: Sequence[Mapping[str, Any]],
    records: Iterable[BehaviorRunRecord],
) -> dict[str, Any]:
    rows = tuple(records)
    if not rows:
        raise BehaviorBaselineError("at least one production-chain record is required")
    dataset_rows = [_normalize_dataset(item) for item in datasets]
    routes = _aggregate_routes(rows)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "baseline_id": _required(baseline_id, "baseline_id"),
        "created_at": _required(created_at, "created_at"),
        "reporting_scope": "behavior_and_operational_characterization_only",
        "production_accuracy_claim": False,
        "versions": {
            "commit_sha": _git_sha(commit_sha, "commit_sha"),
            "tree_sha": _git_sha(tree_sha, "tree_sha"),
            "bundle": dict(bundle),
            "model_policy": dict(model_policy),
            "rag_index_manifest": dict(rag_index_manifest),
            "rag_index_manifest_sha256": _sha256_json(rag_index_manifest),
        },
        "environment_observations": dict(environment_observations or {}),
        "decision_policies": dict(DECISION_POLICY_BASELINE_V1),
        "decision_policy_fingerprints": policy_fingerprints(),
        "datasets": dataset_rows,
        "route_measurements": routes,
        "records": [row.to_dict() for row in rows],
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    validate_behavior_baseline(manifest)
    return manifest


def validate_behavior_baseline(manifest: Mapping[str, Any]) -> None:
    if int(manifest.get("schema_version", 0)) != 1:
        raise BehaviorBaselineError("unsupported behavior baseline schema")
    if manifest.get("production_accuracy_claim") is not False:
        raise BehaviorBaselineError("M0 baseline must not claim production accuracy")
    forbidden = {"accuracy", "production_accuracy", "overall_accuracy"}
    if forbidden & set(manifest):
        raise BehaviorBaselineError("accuracy summary is forbidden in M0 baseline")
    expected = dict(manifest)
    checksum = _sha(expected.pop("manifest_sha256", ""), "manifest_sha256")
    if _sha256_json(expected) != checksum:
        raise BehaviorBaselineError("behavior baseline checksum mismatch")
    if manifest.get("decision_policy_fingerprints") != policy_fingerprints(
        manifest.get("decision_policies") or {},
    ):
        raise BehaviorBaselineError("decision policy fingerprint mismatch")
    for raw in manifest.get("records") or []:
        BehaviorRunRecord.from_mapping(raw)
    if not manifest.get("route_measurements"):
        raise BehaviorBaselineError("route measurements are required")
    for dataset in manifest.get("datasets") or []:
        if dataset.get("classification") not in {
            "provisional", "auto_mapped", "consumed_regression",
        }:
            raise BehaviorBaselineError("unsupported dataset classification")


def verify_replay_environment(
    manifest: Mapping[str, Any],
    *,
    commit_sha: str,
    bundle_version: str,
    bundle_sha256: str,
    rag_index_manifest: Mapping[str, Any],
) -> None:
    validate_behavior_baseline(manifest)
    versions = manifest["versions"]
    expected_bundle = versions["bundle"]
    mismatches = []
    if versions["commit_sha"] != commit_sha:
        mismatches.append("commit_sha")
    if expected_bundle.get("version") != bundle_version:
        mismatches.append("bundle_version")
    if expected_bundle.get("content_hash") != bundle_sha256:
        mismatches.append("bundle_sha256")
    if versions["rag_index_manifest_sha256"] != _sha256_json(rag_index_manifest):
        mismatches.append("rag_index_manifest")
    if mismatches:
        raise BehaviorBaselineError(
            f"replay environment changed: {sorted(mismatches)}"
        )


def write_behavior_baseline(path: str | Path, manifest: Mapping[str, Any]) -> None:
    validate_behavior_baseline(manifest)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_behavior_baseline(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_behavior_baseline(manifest)
    return manifest


def _aggregate_routes(records: Sequence[BehaviorRunRecord]) -> dict[str, Any]:
    grouped: dict[str, list[BehaviorRunRecord]] = {}
    for record in records:
        grouped.setdefault(record.route, []).append(record)
    result = {}
    for route, rows in sorted(grouped.items()):
        latencies = sorted(row.latency_ms for row in rows)
        result[route] = {
            "count": len(rows),
            "latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
            },
            "model_calls": sum(row.model_calls for row in rows),
            "tool_calls": sum(row.tool_calls for row in rows),
            "publication_statuses": _counts(row.publication_status for row in rows),
            "failure_types": _counts(row.failure_type for row in rows),
        }
    return result


def _normalize_dataset(raw: Mapping[str, Any]) -> dict[str, Any]:
    classification = str(raw.get("classification") or "")
    if classification not in {"provisional", "auto_mapped", "consumed_regression"}:
        raise BehaviorBaselineError("dataset classification must be explicit")
    return {
        "dataset_id": _required(raw.get("dataset_id"), "dataset_id"),
        "version": _required(raw.get("version"), "dataset version"),
        "classification": classification,
        "manifest_path": _required(raw.get("manifest_path"), "manifest_path"),
        "manifest_sha256": _sha(raw.get("manifest_sha256"), "manifest_sha256"),
        "cases_sha256": _sha(raw.get("cases_sha256"), "cases_sha256"),
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _percentile(values: Sequence[float], quantile: float) -> float:
    index = max(0, math.ceil(len(values) * quantile) - 1)
    return round(values[index], 3)


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha(value: Any, field: str) -> str:
    normalized = _required(value, field).lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise BehaviorBaselineError(f"{field} must be a SHA-256/64-hex digest")
    return normalized


def _git_sha(value: Any, field: str) -> str:
    normalized = _required(value, field).lower()
    if len(normalized) not in {40, 64} or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise BehaviorBaselineError(f"{field} must be a full Git object digest")
    return normalized


def _required(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise BehaviorBaselineError(f"{field} is required")
    return normalized
