"""在隔离 Chroma collection 中执行项目 RAG 评测并确定性评分。"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable

from evaluation.benchmark import score_bundle
from evaluation.dataset import DatasetBundle
from mcp.knowledge_base import KnowledgeBase


def _read_jsonl(path: Path) -> list[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_retrieval(
    bundle: DatasetBundle,
    *,
    dataset_dir: Path,
    split: str,
    top_k: int = 5,
    rrf_k: int = 60,
    vector_weight: float = 0.0,
    lexical_weight: float = 1.0,
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """加载版本化语料，调用生产 KnowledgeBase，返回 scorer 兼容预测。"""
    corpus = _read_jsonl(dataset_dir / "corpus.jsonl")
    cases = bundle.select(layer="retrieval", split=split, gold_only=False)
    with tempfile.TemporaryDirectory(prefix="dialogpilot-retrieval-") as temp_dir:
        knowledge_base = KnowledgeBase(
            chroma_mode="embedded",
            chroma_path=temp_dir,
            load_default_docs=False,
            collection_name="dialogpilot_eval_retrieval",
            retrieval_rrf_k=rrf_k,
            retrieval_vector_weight=vector_weight,
            retrieval_lexical_weight=lexical_weight,
        )
        inserted = knowledge_base.add_documents(corpus)
        predictions = []
        for case in cases:
            hits = knowledge_base.search(str(case.input.get("query") or ""), top_k=top_k)
            retrieved_ids = []
            for hit in hits:
                document_id = str(hit.get("document_id") or "")
                if document_id and document_id not in retrieved_ids:
                    retrieved_ids.append(document_id)
            predictions.append({
                "case_id": case.case_id,
                "actual": {"retrieved_ids": retrieved_ids},
                "evidence": {"hits": hits},
            })
    report = score_bundle(
        bundle,
        predictions,
        split=split,
        gold_only=False,
        retrieval_k=top_k,
        layers={"retrieval"},
    )
    report["execution_mode"] = "isolated-embedded-production-knowledge-base"
    report["corpus_documents"] = len(corpus)
    report["inserted_chunks"] = inserted
    report["retrieval_config"] = {
        "rrf_k": rrf_k,
        "vector_weight": vector_weight,
        "lexical_weight": lexical_weight,
    }
    return predictions, report


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--split", choices=("dev", "heldout"), default="dev")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--vector-weight", type=float, default=0.0)
    parser.add_argument("--lexical-weight", type=float, default=1.0)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    predictions, report = run_retrieval(
        DatasetBundle.load(args.dataset),
        dataset_dir=args.dataset,
        split=args.split,
        top_k=max(1, args.top_k),
        rrf_k=max(1, args.rrf_k),
        vector_weight=args.vector_weight,
        lexical_weight=args.lexical_weight,
    )
    _write_jsonl(args.predictions, predictions)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "case_count": report["case_count"],
        "pass_rate": report["pass_rate"],
        "metrics": report["layers"].get("retrieval", {}),
        "predictions": str(args.predictions),
        "report": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
