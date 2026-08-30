"""在同一版本化数据集上比较 RAG 检索策略，输出可复现的 dev 选型证据。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from evaluation.dataset import DatasetBundle
from evaluation.retrieval_runner import run_retrieval


DEFAULT_CONFIGS = {
    "vector_only": (1.0, 0.0),
    "bm25_only": (0.0, 1.0),
    "rrf_05_95": (0.05, 0.95),
    "rrf_30_70": (0.30, 0.70),
    "rrf_50_50": (0.50, 0.50),
}


def run_ablation(
    dataset_dir: Path,
    *,
    split: str = "dev",
    top_k: int = 5,
    rrf_k: int = 60,
) -> Dict[str, Any]:
    """逐配置调用生产 KnowledgeBase；配置选择只能使用 dev。"""
    bundle = DatasetBundle.load(dataset_dir)
    rows = []
    for name, (vector_weight, lexical_weight) in DEFAULT_CONFIGS.items():
        _predictions, report = run_retrieval(
            bundle,
            dataset_dir=dataset_dir,
            split=split,
            top_k=top_k,
            rrf_k=rrf_k,
            vector_weight=vector_weight,
            lexical_weight=lexical_weight,
        )
        metrics = dict(report["layers"].get("retrieval") or {})
        rows.append({
            "name": name,
            "vector_weight": vector_weight,
            "lexical_weight": lexical_weight,
            "pass_rate": report["pass_rate"],
            **metrics,
        })
    metric_order = (f"recall_at_{top_k}", "mrr", f"ndcg_at_{top_k}")
    ranked = sorted(
        rows,
        key=lambda row: tuple(float(row.get(metric, 0.0)) for metric in metric_order),
        reverse=True,
    )
    return {
        "dataset_id": bundle.manifest.get("dataset_id"),
        "dataset_checksum": bundle.manifest.get("cases_sha256"),
        "split": split,
        "selection_allowed": split == "dev",
        "selection_metrics": list(metric_order),
        "recommended": ranked[0]["name"] if split == "dev" and ranked else None,
        "results": ranked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--split", choices=("dev", "heldout"), default="dev")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_ablation(
        args.dataset,
        split=args.split,
        top_k=max(1, args.top_k),
        rrf_k=max(1, args.rrf_k),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
