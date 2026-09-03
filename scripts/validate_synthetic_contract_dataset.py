#!/usr/bin/env python3
"""Validate the locked DialogPilot synthetic architecture contract."""
from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "eval" / "dialogpilot-synthetic-contract-v1"
CASE_SCHEMA_VERSION = "dialogpilot-synthetic-contract-case-v1"
CASE_STATUS = "SYNTHETIC_CONTRACT_LOCKED"
REQUIRED_TOP = {
    "schema_version", "case_id", "group_id", "status", "title", "slice",
    "difficulty", "risk", "language", "provenance", "initial_state", "turns",
    "expected_outcome", "deterministic_assertions", "human_rubric",
    "counterfactual", "missing_authoring_inputs", "authoring_notes", "self_check",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise AssertionError(f"{path}:{number}: row is not an object")
        rows.append(value)
    return rows


def python_symbol_exists(path: Path, symbol: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol for node in ast.walk(tree))


def jsonl_id_exists(path: Path, item_id: str) -> bool:
    for row in load_jsonl(path):
        if row.get("id") == item_id or row.get("case_id") == item_id:
            return True
    return False


def resolve_ref(ref: str, catalog: dict[str, Any]) -> bool:
    if ref in catalog["assets"] or ref in catalog["annotations"] or ref in catalog["knowledge_sources"]:
        return True
    if ref.startswith("region://synthetic/"):
        return any(row["region_ref"] == ref for row in catalog["annotations"].values())
    if ref.startswith("source://"):
        rest = ref.removeprefix("source://")
        if "#" not in rest:
            return False
        relative, heading = rest.split("#", 1)
        path = ROOT / relative
        return path.is_file() and heading in path.read_text(encoding="utf-8")
    if ref.startswith("fixture://"):
        rest = ref.removeprefix("fixture://")
        if "::" not in rest:
            return False
        relative, item = rest.split("::", 1)
        path = ROOT / relative
        if not path.is_file():
            return False
        if path.suffix == ".py":
            return python_symbol_exists(path, item)
        if path.suffix == ".jsonl":
            return jsonl_id_exists(path, item)
    return False


def all_case_refs(case: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for values in case["provenance"].values():
        refs.update(values)
    for turn in case["turns"]:
        refs.update(turn["attachment_refs"])
        media = turn["expected"]["media"]
        refs.update(media["required_asset_refs"])
        refs.update(media["required_region_refs"])
        knowledge = turn["expected"]["knowledge"]
        refs.update(knowledge["required_source_refs"])
        refs.update(knowledge["required_evidence_refs"])
    for claim in case["expected_outcome"]["expected_claims"]:
        refs.update(claim["evidence_refs"])
    return refs


def validate_catalog(catalog: dict[str, Any], errors: list[str]) -> None:
    if not catalog.get("disclaimer") or "fabricated" not in catalog["disclaimer"]:
        errors.append("synthetic fixture disclaimer is missing")
    for ref, asset in catalog["assets"].items():
        path = DATA / asset["path"]
        if not path.is_file():
            errors.append(f"{ref}: asset file missing: {path}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != asset["sha256"]:
            errors.append(f"{ref}: checksum mismatch")
    for ref, annotation in catalog["annotations"].items():
        asset = catalog["assets"].get(annotation["asset_ref"])
        if asset is None:
            errors.append(f"{ref}: unknown annotation asset")
            continue
        x0, y0, x1, y1 = annotation["bbox_xyxy"]
        if not (0 <= x0 < x1 <= asset["width"] and 0 <= y0 < y1 <= asset["height"]):
            errors.append(f"{ref}: bbox outside asset")
        if not annotation.get("allowed_observation"):
            errors.append(f"{ref}: empty allowed observation")


def validate_manifest(
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    catalog: dict[str, Any],
    errors: list[str],
) -> None:
    expected_fields = {
        "dataset_id": "dialogpilot-synthetic-contract-v1",
        "dataset_role": "ARCHITECTURE_CONTRACT",
        "schema_version": CASE_SCHEMA_VERSION,
        "authoring_provenance": "MODEL_AUTHORED_SYNTHETIC",
        "lock_status": "SYNTHETIC_CONTRACT_LOCKED",
        "run_status": "NOT_RUN",
        "promotion_allowed": False,
    }
    for field, expected in expected_fields.items():
        if manifest.get(field) != expected:
            errors.append(f"manifest {field} mismatch")
    cases_path = DATA / "cases.jsonl"
    cases_digest = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    if manifest.get("cases_sha256") != cases_digest:
        errors.append("manifest cases checksum mismatch")
    case_ids_digest = hashlib.sha256(
        "\n".join(str(row.get("case_id") or "") for row in rows).encode()
    ).hexdigest()
    if manifest.get("case_ids_sha256") != case_ids_digest:
        errors.append("manifest case IDs checksum mismatch")
    batch_files = {
        str(item.get("file") or "")
        for item in manifest.get("batches", [])
        if isinstance(item, dict)
    }
    asset_files = {
        path.relative_to(DATA).as_posix()
        for path in (DATA / "assets").glob("*") if path.is_file()
    }
    expected_locked = {
        "README.md", "cases.jsonl", "schema.json", "synthetic-fixtures.json",
        *batch_files, *asset_files,
    }
    locked = manifest.get("locked_file_sha256")
    if not isinstance(locked, dict):
        errors.append("manifest locked_file_sha256 is missing")
        return
    if set(locked) != expected_locked:
        errors.append("manifest locked file set mismatch")
    for relative, expected_digest in locked.items():
        path = Path(str(relative))
        if path.is_absolute() or ".." in path.parts:
            errors.append(f"manifest locked path is unsafe: {relative}")
            continue
        absolute = DATA / path
        if not absolute.is_file():
            errors.append(f"manifest locked file missing: {relative}")
            continue
        actual_digest = hashlib.sha256(absolute.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            errors.append(f"manifest locked checksum mismatch: {relative}")


def validate_case(case: dict[str, Any], by_id: dict[str, dict[str, Any]], catalog: dict[str, Any], errors: list[str]) -> None:
    cid = case.get("case_id", "<missing>")
    missing_keys = REQUIRED_TOP - set(case)
    if missing_keys:
        errors.append(f"{cid}: missing top-level keys {sorted(missing_keys)}")
        return
    if case["schema_version"] != CASE_SCHEMA_VERSION:
        errors.append(f"{cid}: wrong schema version")
    if case["status"] != CASE_STATUS:
        errors.append(f"{cid}: synthetic case is not locked")
    if case["missing_authoring_inputs"]:
        errors.append(f"{cid}: resolved synthetic case still lists missing inputs")
    if not case["deterministic_assertions"] or not all(a.get("hard_gate") for a in case["deterministic_assertions"]):
        errors.append(f"{cid}: deterministic hard gates missing")
    if not all(case["self_check"].values()):
        errors.append(f"{cid}: self_check is not fully true")
    pair = case["counterfactual"].get("paired_case_id")
    if pair:
        peer = by_id.get(pair)
        if not peer:
            errors.append(f"{cid}: paired case missing: {pair}")
        else:
            if peer["counterfactual"].get("paired_case_id") != cid:
                errors.append(f"{cid}: counterfactual pairing is not symmetric")
            if peer["group_id"] != case["group_id"]:
                errors.append(f"{cid}: counterfactual pair has different group_id")
            if not case["counterfactual"].get("single_changed_variable"):
                errors.append(f"{cid}: counterfactual variable missing")
    refs = all_case_refs(case)
    for ref in sorted(refs):
        if not resolve_ref(ref, catalog):
            errors.append(f"{cid}: unresolved ref {ref}")
    provenance_assets = set(case["provenance"]["asset_refs"])
    provenance_annotations = set(case["provenance"]["annotation_refs"])
    for media_asset in case["initial_state"]["media_assets"]:
        ref = media_asset["asset_ref"]
        catalog_asset = catalog["assets"].get(ref)
        if catalog_asset is None:
            errors.append(f"{cid}: initial media asset is not catalogued: {ref}")
        elif catalog_asset["sha256"] != media_asset["asset_checksum"]:
            errors.append(f"{cid}: initial media checksum mismatch: {ref}")
    for turn in case["turns"]:
        media = turn["expected"]["media"]
        need = media["media_need"]
        required_assets = set(media["required_asset_refs"])
        required_regions = set(media["required_region_refs"])
        if need in {"L1", "L2"}:
            if not required_assets or not required_regions:
                errors.append(f"{cid}/{turn['turn_id']}: {need} lacks asset or region")
            if not required_assets <= provenance_assets:
                errors.append(f"{cid}/{turn['turn_id']}: required asset absent from provenance")
            region_annotations = {ref for ref in provenance_annotations if catalog["annotations"].get(ref, {}).get("region_ref") in required_regions}
            if not region_annotations:
                errors.append(f"{cid}/{turn['turn_id']}: required region lacks annotation")
        if need == "L0" and (required_assets or required_regions):
            errors.append(f"{cid}/{turn['turn_id']}: L0 unexpectedly requires media")
        if need == "L2" and media["must_not_call_vlm"]:
            errors.append(f"{cid}/{turn['turn_id']}: L2 forbids VLM")
        if need == "L1" and not media["must_not_call_vlm"]:
            errors.append(f"{cid}/{turn['turn_id']}: L1 should remain OCR/layout-only")
    for claim in case["expected_outcome"]["expected_claims"]:
        if not claim["evidence_refs"]:
            errors.append(f"{cid}/{claim['claim_id']}: claim lacks evidence")


def main() -> int:
    errors: list[str] = []
    catalog = load_json(DATA / "synthetic-fixtures.json")
    rows = load_jsonl(DATA / "cases.jsonl")
    manifest = load_json(DATA / "manifest.json")
    validate_catalog(catalog, errors)
    validate_manifest(manifest, rows, catalog, errors)
    ids = [row.get("case_id") for row in rows]
    if len(rows) != 80:
        errors.append(f"expected 80 cases, got {len(rows)}")
    if len(set(ids)) != len(ids):
        errors.append("case_id values are not unique")
    by_id = {row["case_id"]: row for row in rows}
    for row in rows:
        validate_case(row, by_id, catalog, errors)

    expected_slices = {
        "PRODUCT_ID": 20, "INSTALLATION": 20, "DAMAGE": 10, "SCREENSHOT": 10,
        "POLICY_TOOL": 10, "SERVICE_CONTINUITY": 4, "MEMORY": 2, "HANDOFF": 4,
    }
    actual_slices = Counter(row["slice"] for row in rows)
    if dict(actual_slices) != expected_slices:
        errors.append(f"slice counts mismatch: {dict(actual_slices)}")
    media_counts = Counter(row["turns"][-1]["expected"]["media"]["media_need"] for row in rows)
    for need in ("L0", "L1", "L2"):
        if media_counts[need] < 20:
            errors.append(f"global media quota {need} < 20: {media_counts[need]}")
    continuous_media = sum(len(row["turns"]) > 1 and any(turn["attachment_refs"] for turn in row["turns"]) for row in rows)
    if continuous_media < 20:
        errors.append(f"continuous media cases < 20: {continuous_media}")
    counterfactual_pairs = sum(bool(row["counterfactual"]["paired_case_id"]) for row in rows) // 2
    if counterfactual_pairs < 20:
        errors.append(f"counterfactual pairs < 20: {counterfactual_pairs}")
    non_answer = sum(row["expected_outcome"]["terminal_kind"] in {"CLARIFY", "HANDOFF", "AWAIT_SIGNAL"} for row in rows)
    if non_answer < 15:
        errors.append(f"clarify/handoff/await cases < 15: {non_answer}")
    high_risk = sum(row["risk"] in {"HIGH", "CRITICAL"} for row in rows)
    if high_risk < 10:
        errors.append(f"high-risk cases < 10: {high_risk}")

    for batch in manifest["batches"]:
        batch_rows = load_jsonl(DATA / batch["file"])
        if len(batch_rows) != 10:
            errors.append(f"{batch['batch_id']}: expected 10 rows")
        digest = hashlib.sha256((DATA / batch["file"]).read_bytes()).hexdigest()
        if digest != batch["sha256"]:
            errors.append(f"{batch['batch_id']}: manifest checksum mismatch")

    report = {
        "valid": not errors,
        "dataset_id": manifest.get("dataset_id"),
        "lock_status": manifest.get("lock_status"),
        "run_status": manifest.get("run_status"),
        "case_count": len(rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "slice_counts": dict(actual_slices),
        "media_counts": dict(media_counts),
        "continuous_media_cases": continuous_media,
        "counterfactual_pairs": counterfactual_pairs,
        "non_answer_cases": non_answer,
        "high_risk_cases": high_risk,
        "resolved_ref_count": len(set().union(*(all_case_refs(row) for row in rows))),
        "errors": errors,
        "promotion_allowed": False,
    }
    (DATA / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
