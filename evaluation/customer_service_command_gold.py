"""Validation boundary for the project-authored customer-service Command Gold."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from application.route_policy_v2 import FlowActionRegistry


def validate_command_gold(
    root: Path,
    registry: FlowActionRegistry,
) -> Mapping[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["registry_generation"] != registry.generation:
        raise ValueError("Gold and Registry generations differ")
    known_flows = {item.ref.flow_id for item in registry.flows}
    split_ids: dict[str, set[str]] = {}
    counts = {}
    for filename in ("dev.jsonl", "heldout.jsonl"):
        path = root / filename
        expected_file = manifest["files"][filename]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_file["sha256"]:
            raise ValueError(f"frozen Gold checksum differs: {filename}")
        rows = tuple(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        ids = {str(row["case_id"]) for row in rows}
        if len(ids) != len(rows):
            raise ValueError(f"duplicate Gold case id: {filename}")
        for row in rows:
            expected = row["expected"]
            commands = expected["commands"]
            clarification = expected["clarification"]
            if expected["status"] == "RESOLVED" and not commands:
                raise ValueError("RESOLVED Gold requires commands")
            if expected["status"] == "CLARIFY" and not clarification:
                raise ValueError("CLARIFY Gold requires structured clarification")
            if expected["status"] != "CLARIFY" and clarification is not None:
                raise ValueError("non-CLARIFY Gold cannot carry clarification")
            refs = {
                command["flow"]["flow_id"]
                for command in commands if command.get("flow")
            }
            refs.update((clarification or {}).get("candidate_flow_ids") or ())
            if not refs.issubset(known_flows):
                raise ValueError("Gold references a Flow outside the Registry")
        split_ids[filename] = ids
        counts[filename] = len(rows)
    if split_ids["dev.jsonl"] & split_ids["heldout.jsonl"]:
        raise ValueError("dev and heldout case ids overlap")
    return {
        "schema_version": "dialogpilot-customer-service-command-validation-v1",
        "status": "PASS",
        "counts": counts,
        "heldout_consumed": False,
    }
