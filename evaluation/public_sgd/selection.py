"""Deterministic stratified selection from a complete adapted SGD pool."""
from __future__ import annotations

import hashlib
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from evaluation.public_sgd.adapter import ADAPTER_VERSION, DATASET_ID
from evaluation.public_sgd.contracts import (
    SgdAdapterError,
    load_json,
    load_jsonl,
    sha256_file,
    write_json,
    write_jsonl,
)


SELECTION_VERSION = "dialogpilot-sgd-stratified-selection-v2"


def freeze_stratified_subset(
    pool_dir: Path,
    output_dir: Path,
    *,
    per_rule: Mapping[str, int],
    seed: str = "dialogpilot-sgd-command-v1",
) -> Mapping[str, Any]:
    """Select by mapping rule using a stable hash, never file order."""
    pool_manifest = load_json(pool_dir / "manifest.json")
    if pool_manifest.get("publication_status") != "FROZEN_FULL_SPLITS":
        raise SgdAdapterError("selection requires a frozen full-split pool")
    if pool_manifest.get("adapter_version") != ADAPTER_VERSION:
        raise SgdAdapterError("pool adapter version is unsupported")
    if not per_rule or any(value < 1 for value in per_rule.values()):
        raise SgdAdapterError("per-rule limits must be positive")
    if not seed.strip():
        raise SgdAdapterError("selection seed is required")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SgdAdapterError("selection output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pool_dir / "registry.json", output_dir / "registry.json")

    split_manifests: dict[str, Mapping[str, Any]] = {}
    for split in pool_manifest["official_splits_preserved"]:
        cases = load_jsonl(pool_dir / split / "cases.jsonl")
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for case in cases:
            grouped[str(case["provenance"]["mapping_rule"])].append(case)
        missing_rules = set(per_rule).difference(grouped)
        if missing_rules:
            raise SgdAdapterError(
                "pool is missing requested mapping rules: "
                + ",".join(sorted(missing_rules))
            )
        selected: list[Mapping[str, Any]] = []
        for rule, limit in sorted(per_rule.items()):
            ranked = sorted(
                grouped[rule],
                key=lambda case: _selection_key(seed, str(case["case_id"])),
            )
            selected.extend(ranked[:limit])
        selected.sort(key=lambda case: str(case["case_id"]))

        selected_dialogues = {
            str(case["input"]["history"]["dialogue_id"]) for case in selected
        }
        conversations = [
            row for row in load_jsonl(pool_dir / split / "conversations.jsonl")
            if str(row["dialogue_id"]) in selected_dialogues
        ]
        destination = output_dir / split
        destination.mkdir()
        case_count, cases_sha = write_jsonl(destination / "cases.jsonl", selected)
        conversation_count, conversations_sha = write_jsonl(
            destination / "conversations.jsonl", conversations
        )
        counts = Counter(
            str(case["provenance"]["mapping_rule"]) for case in selected
        )
        report = {
            "schema_version": "dialogpilot-sgd-selection-report-v1",
            "split": split,
            "case_count": case_count,
            "conversation_count": conversation_count,
            "selected_mapping_counts": dict(sorted(counts.items())),
            "available_mapping_counts": {
                rule: len(rows) for rule, rows in sorted(grouped.items())
            },
            "cases_sha256": cases_sha,
            "conversations_sha256": conversations_sha,
        }
        write_json(destination / "selection-report.json", report)
        split_manifests[split] = report

    manifest = {
        "schema_version": "dialogpilot-public-dataset-lock-v1",
        "dataset_id": f"{DATASET_ID}-stratified",
        "publication_status": "FROZEN_STRATIFIED_ADAPTED_SPLITS",
        "adapter_version": ADAPTER_VERSION,
        "selection_version": SELECTION_VERSION,
        "selection_seed": seed,
        "per_rule": dict(sorted(per_rule.items())),
        "upstream_uri": pool_manifest["upstream_uri"],
        "upstream_commit": pool_manifest["upstream_commit"],
        "upstream_license": pool_manifest["upstream_license"],
        "parent_manifest_sha256": sha256_file(pool_dir / "manifest.json"),
        "registry_sha256": sha256_file(output_dir / "registry.json"),
        "splits": split_manifests,
    }
    write_json(output_dir / "manifest.json", manifest)
    (output_dir / "README.md").write_text(
        "# DialogPilot SGD Command adapted benchmark\n\n"
        "This is an adapted, deterministic subset of the Schema-Guided "
        "Dialogue dataset. Source annotations are licensed under "
        "[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). "
        "The upstream repository and pinned commit are recorded in "
        "`manifest.json`.\n\n"
        "Scores from this artifact measure DialogPilot Command IR adaptation; "
        "they are not official SGD leaderboard scores and are not production "
        "traffic accuracy.\n",
        encoding="utf-8",
    )
    return manifest


def _selection_key(seed: str, case_id: str) -> tuple[str, str]:
    return (
        hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).hexdigest(),
        case_id,
    )
