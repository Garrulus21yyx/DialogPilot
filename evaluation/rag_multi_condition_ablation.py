"""Evaluate requirement-aware retrieval and post-anchor parent expansion."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from anthropic import AsyncAnthropic

from core.llm_metrics import capture_llm_usage
from core.model_policy import ModelPolicy, ModelRole
from evaluation.rag_context_topology_ablation import (
    CANDIDATE_K,
    CONTEXT_MAX_TOKENS,
    FINAL_K,
    _baseline_chunks,
    _capture_retrieval,
    _context_recall,
    _make_contexts,
    _rerank,
    _zero_usage,
)
from evaluation.rag_hierarchical_adapter import (
    HierarchyIndex,
    budget_aware_mixed_candidates,
    build_hierarchy,
    hierarchy_contract,
    transform_leaf_hit,
)
from evaluation.rag_pipeline.contracts import RagCase
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.fusion import fuse_rankings
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from memory.context import TokenEstimator
from mcp.evidence_set_selector import (
    EVIDENCE_SET_PROMPT_VERSION,
    EvidenceSetSelector,
)
from mcp.knowledge_base import KnowledgeBase
from mcp.query_requirements import (
    REQUIREMENT_PROMPT_VERSION,
    RetrievalRequirement,
)
from mcp.result_reranker import RERANK_PROMPT_VERSION, RerankCandidate

METHODS = (
    "baseline-512-64",
    "requirements-512-64",
    "requirements-dynamic-parent",
)


def _round_robin_union(
    rankings: Mapping[str, Sequence[str]],
    order: Sequence[str],
    *,
    limit: int,
) -> list[str]:
    """Allocate a candidate opportunity to every requirement before depth."""
    result: list[str] = []
    seen = set()
    depth = 0
    while len(result) < limit:
        progressed = False
        for requirement_id in order:
            ranking = rankings.get(requirement_id, ())
            if depth >= len(ranking):
                continue
            progressed = True
            candidate_id = str(ranking[depth])
            if candidate_id not in seen:
                result.append(candidate_id)
                seen.add(candidate_id)
                if len(result) >= limit:
                    break
        if not progressed:
            break
        depth += 1
    return result


def _requirement_rows(capture: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = {str(row["case_id"]): row for row in capture.get("rows") or ()}
    if len(rows) != len(capture.get("rows") or ()):
        raise ValueError("requirement capture contains duplicate case IDs")
    return rows


def _requirements(row: Mapping[str, Any]) -> tuple[RetrievalRequirement, ...]:
    values = tuple(RetrievalRequirement(
        str(item["requirement_id"]), str(item["query"]),
    ) for item in row.get("requirements") or ())
    if not values:
        raise ValueError(f"case={row.get('case_id')} has no requirements")
    return values


def _capture_requirement_retrieval(
    dataset: RagDataset,
    requirement_capture: Mapping[str, Any],
    *,
    hierarchy: HierarchyIndex | None,
) -> list[dict[str, Any]]:
    cases = {case.case_id: case for case in dataset.cases}
    requirement_by_case = _requirement_rows(requirement_capture)
    documents = (
        list(hierarchy.source_documents) if hierarchy is not None else
        [{"id": item.document_id, "title": item.title, "content": item.content}
         for item in dataset.documents]
    )
    with tempfile.TemporaryDirectory(prefix="dialogpilot-requirement-retrieval-") as temp_dir:
        knowledge_base = KnowledgeBase(
            chroma_mode="embedded",
            chroma_path=temp_dir,
            sparse_index_path=str(Path(temp_dir) / "sparse.db"),
            load_default_docs=False,
            collection_name="dialogpilot_requirement_retrieval",
            chunk_strategy="fixed_tokens",
            chunk_max_tokens=4096 if hierarchy is not None else 512,
            chunk_overlap_tokens=0 if hierarchy is not None else 64,
        )
        knowledge_base.add_documents(documents)
        captures = []
        for case_id, requirement_row in requirement_by_case.items():
            case = cases[case_id]
            requirements = _requirements(requirement_row)
            raw_query = str(requirement_row.get("raw_query") or case.query)
            query_rows = list(requirements)
            if raw_query not in {item.query for item in query_rows}:
                query_rows.append(RetrievalRequirement("R0", raw_query))
            per_requirement: dict[str, list[str]] = {}
            source_rankings: dict[str, list[str]] = {}
            hit_by_id: dict[str, dict[str, Any]] = {}
            started = time.perf_counter()
            for requirement in query_rows:
                branches: dict[str, list[str]] = {}
                for source, lexical, vector in (
                    ("bm25", 1.0, 0.0), ("vector", 0.0, 1.0),
                ):
                    hits = knowledge_base.search(
                        requirement.query,
                        top_k=CANDIDATE_K,
                        retrieval_policy={
                            "lexical_weight": lexical,
                            "vector_weight": vector,
                            "rrf_k": 30,
                            "candidate_k": CANDIDATE_K,
                        },
                    )
                    transformed = (
                        [transform_leaf_hit(hit, hierarchy) for hit in hits]
                        if hierarchy is not None else [dict(hit) for hit in hits]
                    )
                    key = f"{requirement.requirement_id}:{source}"
                    branches[key] = [str(hit["chunk_id"]) for hit in transformed]
                    source_rankings[key] = branches[key]
                    hit_by_id.update((str(hit["chunk_id"]), hit) for hit in transformed)
                per_requirement[requirement.requirement_id] = fuse_rankings(
                    branches,
                    weights={
                        f"{requirement.requirement_id}:bm25": 0.75,
                        f"{requirement.requirement_id}:vector": 0.25,
                    },
                    rrf_k=10,
                    top_k=CANDIDATE_K,
                )
            # Requirements receive quota first. Raw remains a safety branch but
            # cannot consume the only first-depth slots of explicit requirements.
            order = [item.requirement_id for item in requirements]
            if "R0" in per_requirement:
                order.append("R0")
            fused_ids = _round_robin_union(
                per_requirement, order, limit=CANDIDATE_K,
            )
            captures.append({
                "case": case,
                "requirements": requirements,
                "query_kind": str(requirement_row.get("query_kind") or "simple"),
                "fused_ids": fused_ids,
                "hit_by_id": hit_by_id,
                "requirement_rankings": per_requirement,
                "source_rankings": source_rankings,
                "query_for_rerank": str(
                    requirement_row.get("standalone_query") or case.query
                ),
                "retrieval_latency_ms": (time.perf_counter() - started) * 1000,
            })
        knowledge_base.close()
    captures.sort(key=lambda item: item["case"].case_id)
    return captures


async def _select_sets(
    client: Any,
    profile: Any,
    captures: Sequence[Mapping[str, Any]],
    *,
    concurrency: int,
) -> list[dict[str, Any]]:
    selector = EvidenceSetSelector(client, profile)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(capture: Mapping[str, Any]) -> dict[str, Any]:
        candidates = tuple(RerankCandidate(
            candidate_id=chunk_id,
            title=str(capture["hit_by_id"][chunk_id].get("title") or ""),
            text=str(capture["hit_by_id"][chunk_id].get("content") or ""),
        ) for chunk_id in capture["fused_ids"])
        async with semaphore:
            with capture_llm_usage() as usage:
                result = await selector.select(
                    str(capture["query_for_rerank"]),
                    capture["requirements"],
                    candidates,
                    fallback_rankings=capture["requirement_rankings"],
                    max_candidates=FINAL_K,
                )
        return {
            **capture,
            "reranked_ids": list(result.selected_ids),
            "selection_assignments": {
                key: list(value) for key, value in result.assignments.items()
            },
            "missing_requirement_ids": list(result.missing_requirement_ids),
            "rerank_error": result.error,
            "rerank_usage": usage.summary()["total"],
        }

    return list(await asyncio.gather(*(one(capture) for capture in captures)))


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(map(float, values))
    position = (len(ordered) - 1) * percentile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _prepare_rows(
    method: str,
    captures: Sequence[Mapping[str, Any]],
    dataset: RagDataset,
    *,
    hierarchy: HierarchyIndex | None,
) -> list[dict[str, Any]]:
    baseline_by_id, baseline_by_document = _baseline_chunks(dataset)
    estimator = TokenEstimator()
    rows = []
    for capture in captures:
        case: RagCase = capture["case"]
        if method == "requirements-dynamic-parent":
            if hierarchy is None:
                raise ValueError("hierarchy is required for dynamic parent")
            contexts = budget_aware_mixed_candidates(
                capture,
                hierarchy,
                candidate_k=len(capture["reranked_ids"]),
                max_tokens=CONTEXT_MAX_TOKENS,
                max_chunks=FINAL_K,
            )
        else:
            contexts = _make_contexts(
                "baseline-512",
                capture,
                dataset,
                baseline_by_id,
                baseline_by_document,
            )
        candidate_metrics = evaluate_ranked_hits(
            case, capture["fused_ids"], capture["hit_by_id"], top_k=CANDIDATE_K,
        )
        selected_metrics = evaluate_ranked_hits(
            case, capture["reranked_ids"], capture["hit_by_id"], top_k=FINAL_K,
        )
        rows.append({
            **capture,
            "contexts": contexts,
            "context_tokens": estimator.estimate(
                "\n\n".join(context.text for context in contexts)
            ),
            "context_recall": _context_recall(case, contexts),
            "retrieval_metrics": candidate_metrics,
            "reranked_metrics": selected_metrics,
        })
    return rows


def _summary(method: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    multi_span = [row for row in rows if len(row["case"].evidence) >= 2]
    planner_parallel = [row for row in rows if row.get("query_kind") == "parallel_composite"]

    def mean(values: Sequence[float]) -> float:
        return statistics.fmean(values) if values else 0.0

    def recall(values: Sequence[Mapping[str, Any]], key: str) -> float:
        if key == "packed":
            return mean([float(row["context_recall"]) for row in values])
        field = "retrieval_metrics" if key == "candidate" else "reranked_metrics"
        return mean([float(row[field]["evidence_recall"]) for row in values])

    latencies = [
        float(row["retrieval_latency_ms"])
        + float(row["rerank_usage"]["latency_ms"]["sum"])
        for row in rows
    ]
    result = {
        "method": method,
        "case_count": len(rows),
        "multi_span_case_count": len(multi_span),
        "planner_parallel_case_count": len(planner_parallel),
        "candidate_evidence_recall_at_20": recall(rows, "candidate"),
        "selected_evidence_recall_at_5": recall(rows, "selected"),
        "packed_evidence_recall": recall(rows, "packed"),
        "multi_span_candidate_recall_at_20": recall(multi_span, "candidate"),
        "multi_span_selected_recall_at_5": recall(multi_span, "selected"),
        "multi_span_packed_completeness": recall(multi_span, "packed"),
        "planner_parallel_packed_completeness": recall(planner_parallel, "packed"),
        "candidate_to_selected_loss": recall(rows, "candidate") - recall(rows, "selected"),
        "selected_to_packed_loss": recall(rows, "selected") - recall(rows, "packed"),
        "mean_context_tokens": mean([float(row["context_tokens"]) for row in rows]),
        "p95_context_tokens": _percentile(
            [float(row["context_tokens"]) for row in rows], 0.95,
        ),
        "p95_retrieval_selection_latency_ms": _percentile(latencies, 0.95),
        "selection_failure_rate": mean([
            float(bool(row["rerank_error"])) for row in rows
        ]),
        "cases_with_missing_requirements_rate": mean([
            float(bool(row.get("missing_requirement_ids"))) for row in rows
        ]),
        "model_calls": sum(int(row["rerank_usage"].get("calls", 0)) for row in rows),
        "model_input_tokens": sum(
            int(row["rerank_usage"].get("input_tokens", 0)) for row in rows
        ),
        "model_output_tokens": sum(
            int(row["rerank_usage"].get("output_tokens", 0)) for row in rows
        ),
    }
    return result


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    case: RagCase = row["case"]
    return {
        "case_id": case.case_id,
        "query": case.query,
        "query_kind": row.get("query_kind", "baseline"),
        "evidence_count": len(case.evidence),
        "requirements": [
            {"requirement_id": item.requirement_id, "query": item.query}
            for item in row.get("requirements", ())
        ],
        "candidate_ids": list(row["fused_ids"]),
        "selected_ids": list(row["reranked_ids"]),
        "selection_assignments": row.get("selection_assignments", {}),
        "missing_requirement_ids": row.get("missing_requirement_ids", []),
        "context_spans": [{
            "context_id": value.chunk_id,
            "source_id": value.document_id,
            "start_char": value.start_char,
            "end_char": value.end_char,
        } for value in row["contexts"]],
        "candidate_recall": row["retrieval_metrics"]["evidence_recall"],
        "selected_recall": row["reranked_metrics"]["evidence_recall"],
        "packed_recall": row["context_recall"],
        "context_tokens": row["context_tokens"],
        "retrieval_latency_ms": row["retrieval_latency_ms"],
        "selection_error": row["rerank_error"],
        "selection_usage": row["rerank_usage"],
    }


async def run_ablation(
    dataset: RagDataset,
    query_capture: Mapping[str, Any],
    requirement_capture: Mapping[str, Any],
    *,
    concurrency: int,
) -> dict[str, Any]:
    for capture in (query_capture, requirement_capture):
        if capture.get("dataset_id") != dataset.manifest.get("dataset_id"):
            raise ValueError("capture and dataset IDs do not match")
    if requirement_capture.get("prompt_version") != REQUIREMENT_PROMPT_VERSION:
        raise ValueError("requirement capture prompt does not match current contract")
    policy = ModelPolicy.from_env()
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if policy.base_url:
        kwargs["base_url"] = policy.base_url
    client = AsyncAnthropic(**kwargs)

    baseline_capture = _capture_retrieval(dataset, query_capture, parent_child=False)
    baseline_capture.sort(key=lambda item: item["case"].case_id)
    baseline_ranked = await _rerank(
        client, policy.profile(ModelRole.RERANK), baseline_capture,
        concurrency=concurrency,
    )
    # Make baseline rows schema-compatible without pretending it had requirements.
    baseline_ranked = [{
        **row,
        "requirements": (),
        "query_kind": "baseline",
        "selection_assignments": {},
        "missing_requirement_ids": [],
    } for row in baseline_ranked]

    fixed_capture = _capture_requirement_retrieval(
        dataset, requirement_capture, hierarchy=None,
    )
    fixed_selected = await _select_sets(
        client, policy.profile(ModelRole.RERANK), fixed_capture,
        concurrency=concurrency,
    )
    hierarchy = build_hierarchy(dataset.documents)
    hierarchical_capture = _capture_requirement_retrieval(
        dataset, requirement_capture, hierarchy=hierarchy,
    )
    hierarchical_selected = await _select_sets(
        client, policy.profile(ModelRole.RERANK), hierarchical_capture,
        concurrency=concurrency,
    )

    rows_by_method = {
        "baseline-512-64": _prepare_rows(
            "baseline-512-64", baseline_ranked, dataset, hierarchy=None,
        ),
        "requirements-512-64": _prepare_rows(
            "requirements-512-64", fixed_selected, dataset, hierarchy=None,
        ),
        "requirements-dynamic-parent": _prepare_rows(
            "requirements-dynamic-parent", hierarchical_selected, dataset,
            hierarchy=hierarchy,
        ),
    }
    summaries = {
        method: _summary(method, rows) for method, rows in rows_by_method.items()
    }
    baseline_rows = {
        row["case"].case_id: row for row in rows_by_method["baseline-512-64"]
    }
    harmful = {}
    gates = {}
    baseline = summaries["baseline-512-64"]
    for method in METHODS[1:]:
        rows = rows_by_method[method]
        harmful[method] = statistics.fmean(
            float(row["context_recall"] < baseline_rows[row["case"].case_id]["context_recall"])
            for row in rows
        )
        candidate = summaries[method]
        gates[method] = {
            "candidate_recall_not_lower": (
                candidate["candidate_evidence_recall_at_20"] + 1e-12
                >= baseline["candidate_evidence_recall_at_20"]
            ),
            "multi_span_completeness_improves": (
                candidate["multi_span_packed_completeness"]
                > baseline["multi_span_packed_completeness"] + 1e-12
            ),
            "harmful_context_rate_zero": harmful[method] == 0.0,
            "p95_context_within_2600": candidate["p95_context_tokens"] <= CONTEXT_MAX_TOKENS,
            "selection_failure_rate_le_1pct": candidate["selection_failure_rate"] <= 0.01,
            "p95_latency_growth_le_20pct": (
                candidate["p95_retrieval_selection_latency_ms"]
                <= baseline["p95_retrieval_selection_latency_ms"] * 1.20
            ),
        }
        gates[method]["passes_all"] = all(gates[method].values())

    return {
        "schema_version": 1,
        "stage": "requirement_retrieval_set_selection_and_packing",
        "dataset_id": dataset.manifest["dataset_id"],
        "case_count": len(query_capture.get("rows") or ()),
        "experiment_contract": {
            "baseline": "Raw/Standalone hybrid → listwise rerank → 512/64 Top-5 packing",
            "requirements_fixed": (
                "typed requirements → per-requirement BM25/Dense → round-robin Top-20 "
                "union → typed set selector Top-5 → 512/64 packing"
            ),
            "requirements_dynamic_parent": (
                "same requirement path over 256-level Haystack leaves; only selected "
                "anchors enter dynamic auto-merge and budget fallback"
            ),
            "candidate_union": (
                "one rank-depth opportunity per explicit requirement, then Raw safety "
                "branch; stable-ID dedup; no Gold"
            ),
            "selector": (
                "requirement-to-candidate mapping, union <=5, exact input IDs, explicit missing"
            ),
            "prompt_versions": {
                "requirements": REQUIREMENT_PROMPT_VERSION,
                "baseline_rerank": RERANK_PROMPT_VERSION,
                "set_selector": EVIDENCE_SET_PROMPT_VERSION,
            },
            "hierarchy": hierarchy_contract(),
        },
        "requirement_capture_summary": {
            key: requirement_capture.get(key) for key in (
                "query_kind_counts", "error_case_count", "total_calls",
                "total_input_tokens", "total_output_tokens",
            )
        },
        "summaries": summaries,
        "harmful_context_rate_vs_baseline": harmful,
        "gates_vs_baseline": gates,
        "recommended": next((
            method for method in METHODS[1:] if gates[method]["passes_all"]
        ), "baseline-512-64"),
        "rows": {
            method: [_public_row(row) for row in rows]
            for method, rows in rows_by_method.items()
        },
        "limitations": [
            "Doc2Dial evidence_count>=2 usually denotes adjacent annotation spans, not necessarily independent user conditions.",
            "This Dev run selects an architecture candidate; it is not an untouched Heldout or production load test.",
            "The 20% latency gate is the existing project experiment budget, not a universal RAG constant.",
            "Generation/Judge is intentionally gated until retrieval, set selection, and packing pass.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--query-capture", type=Path, required=True)
    parser.add_argument("--requirement-capture", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = RagDataset.load(args.dataset)
    query_capture = json.loads(args.query_capture.read_text(encoding="utf-8"))
    requirement_capture = json.loads(args.requirement_capture.read_text(encoding="utf-8"))
    report = asyncio.run(run_ablation(
        dataset, query_capture, requirement_capture, concurrency=args.concurrency,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "summaries": report["summaries"],
        "gates_vs_baseline": report["gates_vs_baseline"],
        "recommended": report["recommended"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
