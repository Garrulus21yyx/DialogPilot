#!/usr/bin/env python3
"""把已通过回归的 Bad Case 导出为版本化 dev regression 数据集。

导出结果始终为 provisional；本脚本没有权限声明 human-reviewed Gold，也不会
生成 heldout。若要提升审核状态，必须再运行显式人工审核工作流。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from evaluation.dataset import DatasetBundle, write_dataset
from services.badcase_registry import BadCase, BadCaseRegistry, BadCaseStatus


EXPORTABLE = {
    BadCaseStatus.REGRESSION_PASS,
    BadCaseStatus.VERIFIED,
    BadCaseStatus.CLOSED,
}


def _case_row(case: BadCase) -> Dict[str, Any]:
    if case.status not in EXPORTABLE:
        raise ValueError(f"{case.badcase_id}: status {case.status.value} is not exportable")
    if not case.fixed_by_commit:
        raise ValueError(f"{case.badcase_id}: fixed_by_commit is required")
    raw_input = case.reproduction.get("input")
    if raw_input is not None and not isinstance(raw_input, dict):
        raise ValueError(f"{case.badcase_id}: reproduction.input must be an object")
    eval_input = dict(raw_input or {})
    input_key = "query" if case.eval_layer == "retrieval" else "message"
    eval_input.setdefault(input_key, case.sanitized_input)
    case_id = case.linked_case_id or f"bc-{case.badcase_id[:16]}"
    return {
        "schema_version": 1,
        "id": case_id,
        "layer": case.eval_layer,
        "split": "dev",
        "group_id": case.semantic_group_id,
        "input": eval_input,
        "expected": case.expected_behavior,
        "tags": [
            "production_badcase", case.stage.value, case.severity.value,
            case.symptom_code, "consumed_regression",
        ],
        "source": {
            "dataset": "dialogpilot-production-badcase",
            "license": "internal-use-only",
            "badcase_id": case.badcase_id,
            "trace_fingerprint": case.fingerprint,
            "fixed_by_commit": case.fixed_by_commit,
        },
        "review": {
            "status": "provisional",
            "notes": "human-triaged production regression; not Gold or heldout",
        },
    }


def export_badcases(
    registry: BadCaseRegistry,
    badcase_ids: Iterable[str],
    output: Path,
    *,
    actor: str,
    corpus_from: Path | None = None,
) -> DatasetBundle:
    """原子构造可校验 bundle，成功后才回写 regression case 引用。"""
    selected = [registry.get(case_id) for case_id in badcase_ids]
    if not selected:
        raise ValueError("at least one badcase id is required")

    existing_rows: List[Dict[str, Any]] = []
    existing_corpus: List[Dict[str, Any]] = []
    manifest: Dict[str, Any] = {}
    if (output / "manifest.json").is_file():
        existing = DatasetBundle.load(output)
        existing_rows = [
            json.loads(line)
            for line in (output / "cases.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        existing_corpus = [dict(item) for item in existing.corpus]
        manifest = dict(existing.manifest)
    elif corpus_from is not None:
        existing_corpus = [dict(item) for item in DatasetBundle.load(corpus_from).corpus]

    rows_by_id = {str(row["id"]): row for row in existing_rows}
    exported: List[tuple[BadCase, str]] = []
    for case in selected:
        row = _case_row(case)
        case_id = str(row["id"])
        prior = rows_by_id.get(case_id)
        if prior is not None and prior != row:
            raise ValueError(f"regression case id conflict: {case_id}")
        rows_by_id[case_id] = row
        exported.append((case, case_id))

    now = datetime.now(timezone.utc).isoformat()
    manifest.update({
        "dataset_id": manifest.get("dataset_id") or output.name,
        "version": manifest.get("version") or "1.0.0",
        "status": "provisional_regression",
        "description": "Versioned production Bad Case dev regressions; never fresh heldout.",
        "updated_at": now,
    })
    bundle = write_dataset(
        output,
        manifest=manifest,
        cases=rows_by_id.values(),
        corpus=existing_corpus,
    )
    for case, case_id in exported:
        registry.link_regression(case.badcase_id, case_id, actor=actor)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--badcase-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument(
        "--corpus-from",
        type=Path,
        help="retrieval case 所引用 corpus 的已验证数据集目录",
    )
    args = parser.parse_args()
    bundle = export_badcases(
        BadCaseRegistry(str(args.database)),
        args.badcase_id,
        args.output,
        actor=args.actor,
        corpus_from=args.corpus_from,
    )
    print(json.dumps(bundle.summary(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
