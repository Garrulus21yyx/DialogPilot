"""Replay captured query transformations against one fixed retrieval index."""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.rag_pipeline.contracts import QueryVariant
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.fusion import fuse_rankings
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_pipeline.query_metrics import evaluate_query_variants
from evaluation.rag_pipeline.selection import paired_group_bootstrap_delta, select_configuration
from mcp.knowledge_base import KnowledgeBase


QUERY_CONFIGS = (
    ("raw", "raw", 1.0),
    *((f"standalone-raw{int(mass * 100):02d}", "standalone", mass) for mass in (0.75, 0.50, 0.25)),
    *((f"multi2-raw{int(mass * 100):02d}", "multi2", mass) for mass in (0.75, 0.50, 0.25)),
    *((f"hyde-raw{int(mass * 100):02d}", "hyde", mass) for mass in (0.75, 0.50, 0.25)),
    *((f"standalone-multi2-raw{int(mass * 100):02d}", "standalone_multi2", mass) for mass in (0.75, 0.50)),
    *((f"all-raw{int(mass * 100):02d}", "all", mass) for mass in (0.75, 0.50)),
)


def _mean(rows: Sequence[Mapping[str, float]], metric: str) -> float:
    return statistics.fmean(float(row[metric]) for row in rows) if rows else 0.0


def _variants(row: Mapping[str, Any], strategy: str) -> list[tuple[str, str, bool]]:
    """Return (stable label, text, vector-only); raw is an invariant first member."""
    values = [("raw", str(row["raw_query"]), False)]
    if strategy in {"standalone", "standalone_multi2", "all"}:
        text = str(row.get("standalone") or "").strip()
        if text and text != values[0][1]:
            values.append(("standalone", text, False))
    if strategy in {"multi2", "standalone_multi2", "all"}:
        for index, text in enumerate(row.get("expansions") or ()):
            text = str(text).strip()
            if text and text not in {item[1] for item in values}:
                values.append((f"expansion-{index}", text, False))
    if strategy in {"hyde", "all"}:
        text = str(row.get("hyde") or "").strip()
        if text:
            values.append(("hyde", text, True))
    return values


def _weights(
    variants: Sequence[tuple[str, str, bool]],
    *,
    raw_mass: float,
    lexical_weight: float,
    vector_weight: float,
) -> dict[str, float]:
    generated = list(variants[1:])
    effective_raw_mass = 1.0 if not generated else raw_mass
    generated_mass = (1.0 - effective_raw_mass) / len(generated) if generated else 0.0
    result = {
        "raw:bm25": effective_raw_mass * lexical_weight,
        "raw:vector": effective_raw_mass * vector_weight,
    }
    for label, _text, vector_only in generated:
        if vector_only:
            result[f"{label}:vector"] = generated_mass
        else:
            result[f"{label}:bm25"] = generated_mass * lexical_weight
            result[f"{label}:vector"] = generated_mass * vector_weight
    return {key: value for key, value in result.items() if value > 0}


def run_query_ablation(
    dataset: RagDataset,
    capture: Mapping[str, Any],
    *,
    chunk_strategy: str = "fixed_tokens",
    chunk_max_tokens: int = 512,
    chunk_overlap_tokens: int = 64,
    lexical_weight: float = 0.75,
    vector_weight: float = 0.25,
    rrf_k: int = 10,
    candidate_k: int = 20,
) -> dict[str, Any]:
    if capture.get("dataset_id") != dataset.manifest.get("dataset_id"):
        raise ValueError("capture and dataset IDs do not match")
    cases_by_id = {case.case_id: case for case in dataset.cases}
    captured_rows = list(capture.get("rows") or ())
    cases = [cases_by_id[str(row["case_id"])] for row in captured_rows]

    with tempfile.TemporaryDirectory(prefix="dialogpilot-rag-query-") as temp_dir:
        knowledge_base = KnowledgeBase(
            chroma_mode="embedded",
            chroma_path=temp_dir,
            load_default_docs=False,
            collection_name="dialogpilot_query_ablation",
            chunk_strategy=chunk_strategy,
            chunk_max_tokens=chunk_max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )
        inserted_chunks = knowledge_base.add_documents([{
            "id": document.document_id,
            "title": document.title,
            "content": document.content,
        } for document in dataset.documents])
        retrieval_captures = []
        for case, row in zip(cases, captured_rows):
            all_variants = _variants(row, "all")
            rankings: dict[str, list[str]] = {}
            hit_by_id: dict[str, Mapping[str, Any]] = {}
            latencies: dict[str, float] = {}
            for label, text, vector_only in all_variants:
                policies = (("vector", 0.0, 1.0),) if vector_only else (
                    ("bm25", 1.0, 0.0), ("vector", 0.0, 1.0),
                )
                for source, bm25, vector in policies:
                    started = time.monotonic()
                    hits = knowledge_base.search(text, top_k=candidate_k, retrieval_policy={
                        "lexical_weight": bm25,
                        "vector_weight": vector,
                        "rrf_k": 30,
                    })
                    key = f"{label}:{source}"
                    latencies[key] = (time.monotonic() - started) * 1000
                    rankings[key] = [str(hit["chunk_id"]) for hit in hits]
                    hit_by_id.update((str(hit["chunk_id"]), hit) for hit in hits)
            retrieval_captures.append({
                "case": case,
                "row": row,
                "rankings": rankings,
                "hit_by_id": hit_by_id,
                "latencies": latencies,
            })

    results = []
    case_metrics_by_config: dict[str, list[dict[str, float]]] = {}
    for config_id, strategy, raw_mass in QUERY_CONFIGS:
        per_case = []
        safety = []
        estimated_latency = []
        transform_input_tokens = []
        transform_output_tokens = []
        for captured in retrieval_captures:
            row = captured["row"]
            variants = _variants(row, strategy)
            weights = _weights(
                variants,
                raw_mass=raw_mass,
                lexical_weight=lexical_weight,
                vector_weight=vector_weight,
            )
            ranked = fuse_rankings(
                captured["rankings"], weights=weights, rrf_k=rrf_k, top_k=candidate_k,
            )
            per_case.append(evaluate_ranked_hits(
                captured["case"], ranked, captured["hit_by_id"], top_k=candidate_k,
            ))
            safety.append(evaluate_query_variants(
                str(row["raw_query"]),
                tuple(QueryVariant(label, text) for label, text, _vector_only in variants),
            ))
            estimated_latency.append(max(captured["latencies"][key] for key in weights))
            used_stages = set()
            if strategy in {"standalone", "standalone_multi2", "all"}:
                used_stages.add("standalone")
            if strategy in {"multi2", "standalone_multi2", "all"}:
                # Multi-query consumes the standalone output in the production chain.
                used_stages.update(("standalone", "multi_query"))
            if strategy in {"hyde", "all"}:
                used_stages.update(("standalone", "hyde"))
            usage = row.get("usage_by_stage") or {}
            transform_input_tokens.append(sum(
                int(usage.get(stage, {}).get("input_tokens", 0)) for stage in used_stages
            ))
            transform_output_tokens.append(sum(
                int(usage.get(stage, {}).get("output_tokens", 0)) for stage in used_stages
            ))
        case_metrics_by_config[config_id] = per_case
        raw_baseline = case_metrics_by_config.get("raw", per_case)
        results.append({
            "config_id": config_id,
            "strategy": strategy,
            "raw_query_mass": raw_mass,
            f"evidence_recall_at_{candidate_k}": _mean(per_case, "evidence_recall"),
            f"document_recall_at_{candidate_k}": _mean(per_case, "document_recall"),
            "mrr": _mean(per_case, "mrr"),
            f"ndcg_at_{candidate_k}": _mean(per_case, "ndcg"),
            "harmful_case_rate_vs_raw": statistics.fmean(
                float(item["evidence_recall"] < baseline["evidence_recall"])
                for item, baseline in zip(per_case, raw_baseline)
            ),
            **{metric: _mean(safety, metric) for metric in (
                "raw_query_retained", "entity_preservation", "negation_preservation",
                "hallucinated_entity_rate", "variant_token_diversity", "vocabulary_expansion_ratio",
            )},
            "mean_estimated_parallel_retrieval_latency_ms": statistics.fmean(estimated_latency),
            "mean_transform_input_tokens": statistics.fmean(transform_input_tokens),
            "mean_transform_output_tokens": statistics.fmean(transform_output_tokens),
            "mean_retrieval_requests": statistics.fmean(
                len(_weights(
                    _variants(captured["row"], strategy), raw_mass=raw_mass,
                    lexical_weight=lexical_weight, vector_weight=vector_weight,
                )) for captured in retrieval_captures
            ),
        })

    constraints = {
        "raw_query_retained": ("eq", 1.0),
        "entity_preservation": ("ge", 0.95),
        "negation_preservation": ("ge", 0.95),
        "hallucinated_entity_rate": ("le", 0.05),
        "harmful_case_rate_vs_raw": ("le", 0.10),
    }
    selection = select_configuration(
        results,
        split=str(capture["split"]),
        hard_constraints=constraints,
        metric_priority=(
            (f"evidence_recall_at_{candidate_k}", "max"),
            ("mrr", "max"),
            (f"ndcg_at_{candidate_k}", "max"),
        ),
        cost_priority=("mean_transform_input_tokens", "mean_retrieval_requests"),
    )
    uncertainty = {}
    recommended = selection.get("recommended")
    if recommended:
        baseline = case_metrics_by_config["raw"]
        chosen = case_metrics_by_config[str(recommended)]
        uncertainty = {
            metric: paired_group_bootstrap_delta(
                chosen, baseline,
                group_ids=[case.group_id for case in cases],
                metric=metric,
            ) for metric in ("evidence_recall", "mrr", "ndcg")
        }
    return {
        "dataset_id": capture["dataset_id"],
        "split": capture["split"],
        "case_count": len(cases),
        "capture_prompt_version": capture["prompt_version"],
        "capture_model_policy": capture["model_policy"],
        "inserted_chunks": inserted_chunks,
        "fixed_retrieval": {
            "chunk_strategy": chunk_strategy,
            "chunk_max_tokens": chunk_max_tokens,
            "chunk_overlap_tokens": chunk_overlap_tokens,
            "lexical_weight": lexical_weight,
            "vector_weight": vector_weight,
            "rrf_k": rrf_k,
            "candidate_k": candidate_k,
        },
        "weight_semantics": (
            "raw_query_mass is reserved for raw BM25/vector retrieval; remaining mass is split "
            "equally over generated variants; each non-HyDE variant keeps the fixed 0.75/0.25 "
            "lexical/vector ratio; HyDE is vector-only and never evidence"
        ),
        "selection": selection,
        "recommended_paired_bootstrap_vs_raw": uncertainty,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    report = run_query_ablation(RagDataset.load(args.dataset), capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "case_count": report["case_count"],
        "recommended": report["selection"]["recommended"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
