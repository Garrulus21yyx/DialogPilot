"""Read-only index over completed command-primary evaluation runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LAYERS = ("A", "B", "C", "D")
CATALOG_SCHEMA_VERSION = "command-primary-scorecard-catalog-v1"
SCORECARD_STATUSES = frozenset(
    {
        "PASS",
        "FAIL",
        "INCONCLUSIVE",
        "NOT_RUN",
        "BLOCKED",
    }
)


@dataclass(frozen=True)
class _RunIndex:
    status: str
    reason_code: str | None
    status_pointer: str | None
    source: Mapping[str, Any]
    report: Mapping[str, Any] | None


def build_scorecard(
    catalog: Mapping[str, Any],
    *,
    base_dir: str | Path = ".",
) -> dict[str, Any]:
    """Index the manifest/report pairs explicitly named by ``catalog``."""

    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError(f"catalog.schema_version must be {CATALOG_SCHEMA_VERSION}")
    scorecard_id = _required_text(catalog, "scorecard_id")
    runs = catalog.get("runs")
    if not isinstance(runs, list):
        raise ValueError("catalog.runs must be a list")

    layers: dict[str, list[dict[str, Any]]] = {layer: [] for layer in LAYERS}
    root = Path(base_dir)
    for run in runs:
        if not isinstance(run, Mapping):
            raise ValueError("each catalog run must be an object")
        entry_id = _required_text(run, "entry_id")
        expected_run_id = _required_text(run, "run_id")
        indexed = _index_run(run, root, expected_run_id)
        contributions = run.get("contributions")
        if not isinstance(contributions, list) or not contributions:
            raise ValueError(f"{entry_id}.contributions must be a non-empty list")
        for contribution in contributions:
            if not isinstance(contribution, Mapping):
                raise ValueError(f"{entry_id} contribution must be an object")
            layer = _required_text(contribution, "layer")
            if layer not in layers:
                raise ValueError(f"unsupported scorecard layer: {layer}")
            layers[layer].append(
                _build_contribution(entry_id, expected_run_id, contribution, indexed)
            )

    return {
        "schema_version": "command-primary-scorecard-v1",
        "scorecard_id": scorecard_id,
        "layers": layers,
    }


def _index_run(
    run: Mapping[str, Any],
    base_dir: Path,
    expected_run_id: str,
) -> _RunIndex:
    manifest_ref = _required_text(run, "manifest")
    report_ref = _required_text(run, "report")
    expected_manifest_sha256 = _required_text(run, "manifest_sha256")
    expected_report_sha256 = _required_text(run, "report_sha256")
    manifest_path = base_dir / manifest_ref
    report_path = base_dir / report_ref
    source: dict[str, Any] = {
        "manifest": {
            "path": manifest_ref,
            "expected_sha256": expected_manifest_sha256,
            "sha256": None,
        },
        "report": {
            "path": report_ref,
            "expected_sha256": expected_report_sha256,
            "sha256": None,
        },
    }
    try:
        manifest_bytes = manifest_path.read_bytes()
        source["manifest"]["sha256"] = _sha256(manifest_bytes)
        report_bytes = report_path.read_bytes()
        source["report"]["sha256"] = _sha256(report_bytes)
        manifest = json.loads(manifest_bytes)
        report = json.loads(report_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        return _RunIndex(
            status="BLOCKED",
            reason_code="RUN_ARTIFACT_UNAVAILABLE",
            status_pointer=None,
            source={**source, "detail": str(exc)},
            report=None,
        )
    if (
        source["manifest"]["sha256"] != expected_manifest_sha256
        or source["report"]["sha256"] != expected_report_sha256
    ):
        return _blocked(source, "RUN_ARTIFACT_SHA256_MISMATCH")
    if not isinstance(manifest, Mapping) or not isinstance(report, Mapping):
        return _blocked(source, "RUN_ARTIFACT_NOT_OBJECT")
    actual_ids = (manifest.get("run_id"), report.get("run_id"))
    if actual_ids != (expected_run_id, expected_run_id):
        return _blocked(source, "RUN_ID_MISMATCH")

    status, reason_code, pointer = _report_status(report)
    return _RunIndex(status, reason_code, pointer, source, report)


def _report_status(
    report: Mapping[str, Any],
) -> tuple[str, str | None, str | None]:
    for field in ("evaluation_status", "status"):
        value = report.get(field)
        if value in SCORECARD_STATUSES:
            return str(value), _optional_text(report.get("reason_code")), f"/{field}"
        if value == "BLOCKED_MISSING_SYSTEM_CAPABILITY":
            return "BLOCKED", str(value), f"/{field}"

    run_status = report.get("run_status")
    if run_status == "NOT_RUN":
        return "NOT_RUN", _optional_text(report.get("reason_code")), "/run_status"
    if isinstance(run_status, str) and run_status.startswith("BLOCKED"):
        return "BLOCKED", run_status, "/run_status"
    if isinstance(run_status, str) and run_status.startswith("COMPLETED"):
        return "INCONCLUSIVE", "CANONICAL_JUDGMENT_MISSING", "/run_status"
    return "BLOCKED", "CANONICAL_JUDGMENT_MISSING", None


def _build_contribution(
    entry_id: str,
    run_id: str,
    contribution: Mapping[str, Any],
    indexed: _RunIndex,
) -> dict[str, Any]:
    row = {
        "entry_id": entry_id,
        "run_id": run_id,
        "status": indexed.status,
        "reason_code": indexed.reason_code,
        "status_pointer": indexed.status_pointer,
        "source": dict(indexed.source),
        "headline": {},
    }
    headline = contribution.get("headline", {})
    if not isinstance(headline, Mapping):
        raise ValueError(f"{entry_id}.headline must be an object")
    if indexed.report is None:
        return row
    try:
        row["headline"] = {
            str(name): {
                "report_pointer": str(pointer),
                "value": resolve_json_pointer(indexed.report, str(pointer)),
            }
            for name, pointer in headline.items()
        }
    except (KeyError, IndexError, TypeError, ValueError):
        row["status"] = "BLOCKED"
        row["reason_code"] = "REPORT_POINTER_UNRESOLVED"
        row["headline"] = {}
    return row


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer against an already-loaded report."""

    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must be empty or start with '/'")
    value = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping):
            value = value[token]
        elif isinstance(value, list):
            value = value[int(token)]
        else:
            raise TypeError("JSON Pointer traversed a scalar")
    return value


def _blocked(source: Mapping[str, Any], reason_code: str) -> _RunIndex:
    return _RunIndex("BLOCKED", reason_code, None, source, None)


def _required_text(value: Mapping[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{field} must be a non-empty string")
    return result


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
