"""对分层数据集的实际输出做确定性评分；LLM Judge 保持独立信号。"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from evaluation.dataset import DatasetBundle, EvalCase


class PredictionError(ValueError):
    """预测文件缺失、重复或输出代数不支持。"""


def score_bundle(
    bundle: DatasetBundle,
    predictions: Iterable[Mapping[str, Any]],
    *,
    split: str,
    gold_only: bool = True,
    retrieval_k: int = 5,
) -> Dict[str, Any]:
    """按层评分并返回逐例证据；不把 auto-mapped 样本混入 gold 指标。"""
    prediction_map: Dict[str, Dict[str, Any]] = {}
    for row in predictions:
        case_id = str(row.get("case_id") or "")
        if not case_id or case_id in prediction_map:
            raise PredictionError("predictions require unique non-empty case_id")
        prediction_map[case_id] = dict(row.get("actual") or {})
    cases = bundle.select(split=split, gold_only=gold_only)
    missing = [case.case_id for case in cases if case.case_id not in prediction_map]
    if missing:
        raise PredictionError(f"missing predictions: {missing}")

    outcomes: List[Dict[str, Any]] = []
    grouped: Dict[str, List[Dict[str, float]]] = defaultdict(list)
    intent_pairs: List[tuple[str, str]] = []
    for case in cases:
        actual = prediction_map[case.case_id]
        metrics = _score_case(case, actual, retrieval_k=retrieval_k)
        grouped[case.layer].append(metrics)
        if case.layer == "intent":
            intent_pairs.append((str(case.expected["intent"]), str(actual.get("intent") or "")))
        outcomes.append({
            "case_id": case.case_id,
            "layer": case.layer,
            "split": case.split,
            "metrics": metrics,
            "passed": all(value >= 1.0 for value in metrics.values()),
        })

    layer_metrics: Dict[str, Dict[str, float]] = {}
    for layer, rows in grouped.items():
        names = sorted({name for row in rows for name in row})
        layer_metrics[layer] = {
            name: round(statistics.mean(row[name] for row in rows if name in row), 4)
            for name in names
        }
    if intent_pairs:
        layer_metrics.setdefault("intent", {}).update(_classification_metrics(intent_pairs))

    return {
        "dataset_id": bundle.manifest.get("dataset_id"),
        "dataset_version": bundle.manifest.get("version"),
        "dataset_checksum": bundle.manifest.get("cases_sha256"),
        "split": split,
        "gold_only": gold_only,
        "case_count": len(cases),
        "pass_rate": round(sum(item["passed"] for item in outcomes) / len(outcomes), 4) if outcomes else 0.0,
        "layers": layer_metrics,
        "outcomes": outcomes,
    }


def _score_case(case: EvalCase, actual: Mapping[str, Any], *, retrieval_k: int) -> Dict[str, float]:
    if case.layer == "intent":
        return {"exact_match": float(str(actual.get("intent")) == str(case.expected["intent"]))}
    if case.layer == "routing":
        owners = set(map(str, actual.get("owners") or []))
        expected_owners = set(map(str, case.expected["owners"]))
        tasks = set(map(str, actual.get("task_ids") or []))
        expected_tasks = set(map(str, case.expected["task_ids"]))
        union = owners | expected_owners
        return {
            "owner_exact_match": float(owners == expected_owners),
            "owner_jaccard": len(owners & expected_owners) / len(union) if union else 1.0,
            "task_exact_match": float(tasks == expected_tasks),
            "required_owner_coverage": len(owners & expected_owners) / len(expected_owners) if expected_owners else 1.0,
            "fanout_efficiency": len(owners & expected_owners) / len(owners) if owners else 0.0,
        }
    if case.layer == "retrieval":
        retrieved = list(map(str, actual.get("retrieved_ids") or []))[:retrieval_k]
        relevant = set(map(str, case.expected["relevant_ids"]))
        hits = [index for index, item in enumerate(retrieved, 1) if item in relevant]
        recall = len(set(retrieved) & relevant) / len(relevant) if relevant else 1.0
        reciprocal_rank = 1.0 / hits[0] if hits else 0.0
        dcg = sum(1.0 / math.log2(index + 1) for index in hits)
        ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(relevant), retrieval_k) + 1))
        return {
            f"recall_at_{retrieval_k}": recall,
            "mrr": reciprocal_rank,
            f"ndcg_at_{retrieval_k}": dcg / ideal if ideal else 1.0,
        }
    expected = {str(key): bool(value) for key, value in dict(case.expected["assertions"]).items()}
    observed = {str(key): bool(value) for key, value in dict(actual.get("assertions") or {}).items()}
    matched = sum(observed.get(key) is value for key, value in expected.items())
    return {
        "assertion_pass_rate": matched / len(expected) if expected else 1.0,
        "all_assertions_pass": float(matched == len(expected)),
    }


def _classification_metrics(pairs: Sequence[tuple[str, str]]) -> Dict[str, float]:
    labels = sorted({label for pair in pairs for label in pair})
    f1_values: List[float] = []
    for label in labels:
        tp = sum(expected == label and actual == label for expected, actual in pairs)
        fp = sum(expected != label and actual == label for expected, actual in pairs)
        fn = sum(expected == label and actual != label for expected, actual in pairs)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1_values.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    oos = [(expected, actual) for expected, actual in pairs if expected == "other"]
    return {
        "accuracy": round(sum(expected == actual for expected, actual in pairs) / len(pairs), 4),
        "macro_f1": round(statistics.mean(f1_values), 4) if f1_values else 0.0,
        "oos_recall": round(sum(actual == "other" for _, actual in oos) / len(oos), 4) if oos else 0.0,
    }


def read_predictions(path: str | Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _main() -> int:
    parser = argparse.ArgumentParser(description="Score DialogPilot layered benchmark predictions")
    parser.add_argument("dataset")
    parser.add_argument("predictions")
    parser.add_argument("--split", choices=("dev", "heldout"), required=True)
    parser.add_argument("--include-auto-mapped", action="store_true")
    parser.add_argument("--retrieval-k", type=int, default=5)
    args = parser.parse_args()
    report = score_bundle(
        DatasetBundle.load(args.dataset),
        read_predictions(args.predictions),
        split=args.split,
        gold_only=not args.include_auto_mapped,
        retrieval_k=max(1, args.retrieval_k),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
