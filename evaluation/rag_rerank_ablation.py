"""Evaluate the production listwise reranker on fixed first-stage candidates."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import tempfile
from pathlib import Path
from typing import Any, Mapping

from anthropic import AsyncAnthropic

from core.llm_metrics import capture_llm_usage
from core.model_policy import ModelPolicy, ModelRole
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.fusion import fuse_rankings
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_pipeline.selection import paired_group_bootstrap_delta
from evaluation.rag_query_ablation import _variants, _weights
from mcp.knowledge_base import KnowledgeBase
from mcp.result_reranker import RERANK_PROMPT_VERSION, RerankCandidate, ResultReranker


def _mean(rows: list[Mapping[str, float]], metric: str) -> float:
    return statistics.fmean(float(row[metric]) for row in rows) if rows else 0.0


async def run_rerank_ablation(
    dataset: RagDataset,
    query_capture: Mapping[str, Any],
    *,
    concurrency: int = 2,
    candidate_k: int = 20,
    final_k: int = 5,
    previous_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    policy = ModelPolicy.from_env()
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if policy.base_url:
        client_kwargs["base_url"] = policy.base_url
    client = AsyncAnthropic(**client_kwargs)
    profile = policy.profile(ModelRole.RERANK)
    reranker = ResultReranker(client, profile)
    cases_by_id = {case.case_id: case for case in dataset.cases}
    rows = list(query_capture.get("rows") or ())

    with tempfile.TemporaryDirectory(prefix="dialogpilot-rag-rerank-") as temp_dir:
        knowledge_base = KnowledgeBase(
            chroma_mode="embedded", chroma_path=temp_dir, load_default_docs=False,
            collection_name="dialogpilot_rerank_ablation",
            chunk_strategy="fixed_tokens", chunk_max_tokens=512, chunk_overlap_tokens=64,
        )
        inserted_chunks = knowledge_base.add_documents([{
            "id": document.document_id, "title": document.title, "content": document.content,
        } for document in dataset.documents])
        first_stage = []
        for row in rows:
            case = cases_by_id[str(row["case_id"])]
            variants = _variants(row, "standalone")
            weights = _weights(
                variants, raw_mass=0.25, lexical_weight=0.75, vector_weight=0.25,
            )
            rankings = {}
            hit_by_id = {}
            for label, text, _vector_only in variants:
                for source, lexical, vector in (
                    ("bm25", 1.0, 0.0), ("vector", 0.0, 1.0),
                ):
                    hits = knowledge_base.search(text, top_k=candidate_k, retrieval_policy={
                        "lexical_weight": lexical, "vector_weight": vector, "rrf_k": 30,
                    })
                    rankings[f"{label}:{source}"] = [str(hit["chunk_id"]) for hit in hits]
                    hit_by_id.update((str(hit["chunk_id"]), hit) for hit in hits)
            fused = fuse_rankings(rankings, weights=weights, rrf_k=10, top_k=candidate_k)
            first_stage.append((case, row, fused, hit_by_id))

    semaphore = asyncio.Semaphore(max(1, concurrency))
    previous_rows = {
        str(row["case_id"]): row for row in (previous_report or {}).get("rows", ())
    }

    async def one(item: tuple[Any, ...]) -> dict[str, Any]:
        case, row, fused, hit_by_id = item
        previous = previous_rows.get(case.case_id)
        if previous is not None and previous.get("error") is None:
            return dict(previous)
        candidates = tuple(RerankCandidate(
            candidate_id=chunk_id,
            title=str(hit_by_id[chunk_id].get("title") or ""),
            text=str(hit_by_id[chunk_id].get("content") or ""),
        ) for chunk_id in fused)
        async with semaphore:
            with capture_llm_usage() as usage:
                result = await reranker.rerank(str(row.get("standalone") or case.query), candidates)
        baseline = evaluate_ranked_hits(case, fused, hit_by_id, top_k=final_k)
        reranked = evaluate_ranked_hits(case, result.ordered_ids, hit_by_id, top_k=final_k)
        candidate_metrics = evaluate_ranked_hits(case, fused, hit_by_id, top_k=candidate_k)
        current_usage = usage.summary()["total"]
        if previous is not None:
            prior_usage = previous.get("usage") or {}
            for field in ("calls", "errors", "input_tokens", "output_tokens"):
                current_usage[field] = int(current_usage.get(field, 0)) + int(prior_usage.get(field, 0))
            current_usage["latency_ms"]["sum"] = (
                float(current_usage["latency_ms"]["sum"])
                + float(prior_usage.get("latency_ms", {}).get("sum", 0.0))
            )
        return {
            "case_id": case.case_id,
            "group_id": case.group_id,
            "candidate_ids": list(fused),
            "reranked_ids": list(result.ordered_ids),
            "model_ordered_ids": list(result.model_ordered_ids),
            "error": result.error,
            "candidate_metrics": candidate_metrics,
            "baseline_top5": baseline,
            "reranked_top5": reranked,
            "usage": current_usage,
        }

    captures = list(await asyncio.gather(*(one(item) for item in first_stage)))
    baseline_rows = [row["baseline_top5"] for row in captures]
    reranked_rows = [row["reranked_top5"] for row in captures]
    candidate_rows = [row["candidate_metrics"] for row in captures]
    group_ids = [str(row["group_id"]) for row in captures]
    candidate_to_final_loss_rate = statistics.fmean(
        float(candidate["evidence_recall"] > reranked["evidence_recall"])
        for candidate, reranked in zip(candidate_rows, reranked_rows)
    )
    rerank_harmful_rate = statistics.fmean(
        float(baseline["evidence_recall"] > reranked["evidence_recall"])
        for baseline, reranked in zip(baseline_rows, reranked_rows)
    )
    return {
        "dataset_id": dataset.manifest["dataset_id"],
        "split": query_capture["split"],
        "case_count": len(captures),
        "inserted_chunks": inserted_chunks,
        "fixed_first_stage": {
            "query_strategy": "raw 0.25 + standalone 0.75",
            "lexical_weight": 0.75, "vector_weight": 0.25,
            "rrf_k": 10, "candidate_k": candidate_k, "final_k": final_k,
        },
        "reranker": {
            "prompt_version": RERANK_PROMPT_VERSION,
            "model": profile.to_dict(),
            "failure_count": sum(row["error"] is not None for row in captures),
            "input_tokens": sum(int(row["usage"]["input_tokens"]) for row in captures),
            "output_tokens": sum(int(row["usage"]["output_tokens"]) for row in captures),
            "latency_ms_p50": statistics.median(
                float(row["usage"]["latency_ms"]["sum"]) for row in captures
            ),
        },
        "candidate_recall_at_20": _mean(candidate_rows, "evidence_recall"),
        "no_rerank": {
            "evidence_recall_at_5": _mean(baseline_rows, "evidence_recall"),
            "mrr_at_5": _mean(baseline_rows, "mrr"),
            "ndcg_at_5": _mean(baseline_rows, "ndcg"),
        },
        "llm_rerank": {
            "evidence_recall_at_5": _mean(reranked_rows, "evidence_recall"),
            "mrr_at_5": _mean(reranked_rows, "mrr"),
            "ndcg_at_5": _mean(reranked_rows, "ndcg"),
            "candidate_to_final_evidence_loss_rate": candidate_to_final_loss_rate,
            "harmful_case_rate_vs_no_rerank": rerank_harmful_rate,
        },
        "paired_bootstrap_rerank_vs_no_rerank": {
            metric: paired_group_bootstrap_delta(
                reranked_rows, baseline_rows, group_ids=group_ids, metric=metric,
            ) for metric in ("evidence_recall", "mrr", "ndcg")
        },
        "deployment_gate": {
            "max_harmful_case_rate": 0.05,
            "max_failure_rate": 0.01,
            "passes": (
                rerank_harmful_rate <= 0.05
                and sum(row["error"] is not None for row in captures) / len(captures) <= 0.01
            ),
            "rule": (
                "reranking may reduce top-5 evidence recall in at most 5% of Dev cases "
                "and typed fallback may occur in at most 1%"
            ),
        },
        "rows": captures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("query_capture", type=Path)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous_report = (
        json.loads(args.resume_report.read_text(encoding="utf-8"))
        if args.resume_report else None
    )
    report = asyncio.run(run_rerank_ablation(
        RagDataset.load(args.dataset),
        json.loads(args.query_capture.read_text(encoding="utf-8")),
        concurrency=args.concurrency,
        previous_report=previous_report,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "case_count": report["case_count"],
        "failure_count": report["reranker"]["failure_count"],
        "deployment_gate": report["deployment_gate"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
