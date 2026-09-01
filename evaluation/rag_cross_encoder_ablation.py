"""Compare a local cross-encoder cascade with the captured Flash LLM reranker."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder
else:
    CrossEncoder = Any

from evaluation.rag_context_topology_ablation import _capture_retrieval, _zero_usage
from evaluation.rag_hierarchical_adapter import build_hierarchy
from evaluation.rag_multi_condition_ablation import (
    FINAL_K,
    _capture_requirement_retrieval,
    _prepare_rows,
    _public_row,
    _summary,
)
from evaluation.rag_pipeline.dataset import RagDataset

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"


def _rank(
    model: CrossEncoder,
    query: str,
    candidate_ids: Sequence[str],
    hit_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], dict[str, float]]:
    pairs = [
        (query, str(hit_by_id[candidate_id].get("content") or ""))
        for candidate_id in candidate_ids
    ]
    scores = [float(value) for value in model.predict(
        pairs, batch_size=16, show_progress_bar=False,
    )]
    by_id = dict(zip(candidate_ids, scores, strict=True))
    ordered = sorted(candidate_ids, key=lambda value: (-by_id[value], value))
    return ordered, by_id


def _select_requirements(
    model: CrossEncoder,
    capture: Mapping[str, Any],
    *,
    anchors_per_requirement: int = 1,
) -> dict[str, Any]:
    if anchors_per_requirement < 1:
        raise ValueError("anchors_per_requirement must be positive")
    started = time.perf_counter()
    rankings: dict[str, list[str]] = {}
    scores: dict[str, dict[str, float]] = {}
    for requirement in capture["requirements"]:
        ordered, values = _rank(
            model,
            requirement.query,
            capture["fused_ids"],
            capture["hit_by_id"],
        )
        rankings[requirement.requirement_id] = ordered
        scores[requirement.requirement_id] = values
    selected: list[str] = []
    assignments: dict[str, list[str]] = {}
    # The coverage invariant owns the first pass: one anchor opportunity per
    # requirement. Remaining slots maximize the best cross-encoder support.
    for requirement in capture["requirements"]:
        anchors = rankings[requirement.requirement_id][:anchors_per_requirement]
        assignments[requirement.requirement_id] = list(anchors)
        for candidate_id in anchors:
            if candidate_id not in selected and len(selected) < FINAL_K:
                selected.append(candidate_id)
    remaining = sorted(
        (value for value in capture["fused_ids"] if value not in selected),
        key=lambda value: (
            -max(scores[key][value] for key in scores), value,
        ),
    )
    selected.extend(remaining[:max(0, FINAL_K - len(selected))])
    latency_ms = (time.perf_counter() - started) * 1000
    return {
        **capture,
        "reranked_ids": selected[:FINAL_K],
        "selection_assignments": assignments,
        "missing_requirement_ids": [],
        "rerank_error": None,
        "rerank_usage": {
            **_zero_usage(),
            "latency_ms": {
                "p50": latency_ms, "p95": latency_ms,
                "max": latency_ms, "sum": latency_ms,
            },
        },
        "cross_encoder_scores": scores,
    }


def _select_baseline(model: CrossEncoder, capture: Mapping[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    ordered, scores = _rank(
        model,
        str(capture["query_for_rerank"]),
        capture["fused_ids"],
        capture["hit_by_id"],
    )
    latency_ms = (time.perf_counter() - started) * 1000
    cutoff_margin = (
        scores[ordered[FINAL_K - 1]] - scores[ordered[FINAL_K]]
        if len(ordered) > FINAL_K else float("inf")
    )
    return {
        **capture,
        "requirements": (),
        "query_kind": "baseline",
        "reranked_ids": ordered[:FINAL_K],
        "selection_assignments": {},
        "missing_requirement_ids": [],
        "rerank_error": None,
        "rerank_usage": {
            **_zero_usage(),
            "latency_ms": {
                "p50": latency_ms, "p95": latency_ms,
                "max": latency_ms, "sum": latency_ms,
            },
        },
        "cross_encoder_scores": {"query": scores},
        "cross_encoder_cutoff_margin": cutoff_margin,
    }


def _cascade_replay(
    cross_rows: Sequence[Mapping[str, Any]],
    llm_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Replay low-margin fallback without another provider call."""
    llm_by_case = {str(row["case_id"]): row for row in llm_rows}
    ordered = sorted(
        cross_rows,
        key=lambda row: (
            float(row.get("cross_encoder_cutoff_margin", float("inf"))),
            row["case"].case_id,
        ),
    )
    result = []
    for fallback_count in sorted({
        round(len(ordered) * rate) for rate in (
            0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0,
        )
    }):
        fallback_ids = {
            row["case"].case_id for row in ordered[:fallback_count]
        }
        recalls = []
        latencies = []
        llm_input_tokens = 0
        llm_output_tokens = 0
        for row in cross_rows:
            case_id = row["case"].case_id
            cross_latency = float(row["rerank_usage"]["latency_ms"]["sum"])
            if case_id in fallback_ids:
                llm = llm_by_case[case_id]
                recalls.append(float(llm["packed_recall"]))
                usage = llm["selection_usage"]
                llm_input_tokens += int(usage.get("input_tokens", 0))
                llm_output_tokens += int(usage.get("output_tokens", 0))
                latencies.append(
                    cross_latency + float(usage["latency_ms"]["sum"])
                )
            else:
                recalls.append(float(row["context_recall"]))
                latencies.append(cross_latency)
        result.append({
            "fallback_count": fallback_count,
            "fallback_rate": fallback_count / len(ordered) if ordered else 0.0,
            "packed_recall": statistics.fmean(recalls) if recalls else 0.0,
            "llm_input_tokens": llm_input_tokens,
            "llm_output_tokens": llm_output_tokens,
            "p95_selection_latency_ms": _percentile(latencies, 0.95),
        })
    return result


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(map(float, values))
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def run(
    dataset: RagDataset,
    query_capture: Mapping[str, Any],
    requirement_capture: Mapping[str, Any],
    *,
    model_name: str,
    llm_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    try:
        from sentence_transformers import CrossEncoder as RuntimeCrossEncoder
    except ImportError as exc:
        raise RuntimeError(
            "cross-encoder evaluation requires: pip install -r requirements-semantic.txt"
        ) from exc
    loaded = time.perf_counter()
    model = RuntimeCrossEncoder(model_name, max_length=512)
    model_load_ms = (time.perf_counter() - loaded) * 1000

    baseline_capture = _capture_retrieval(dataset, query_capture, parent_child=False)
    baseline_ranked = [_select_baseline(model, value) for value in baseline_capture]
    fixed_capture = _capture_requirement_retrieval(
        dataset, requirement_capture, hierarchy=None,
    )
    fixed_ranked = [_select_requirements(model, value) for value in fixed_capture]
    fixed_ranked_q2 = [
        _select_requirements(model, value, anchors_per_requirement=2)
        for value in fixed_capture
    ]
    hierarchy = build_hierarchy(dataset.documents)
    hierarchy_capture = _capture_requirement_retrieval(
        dataset, requirement_capture, hierarchy=hierarchy,
    )
    hierarchy_ranked = [_select_requirements(model, value) for value in hierarchy_capture]
    hierarchy_ranked_q2 = [
        _select_requirements(model, value, anchors_per_requirement=2)
        for value in hierarchy_capture
    ]
    rows_by_method = {
        "cross-encoder-baseline-512-64": _prepare_rows(
            "baseline-512-64", baseline_ranked, dataset, hierarchy=None,
        ),
        "cross-encoder-requirements-512-64": _prepare_rows(
            "requirements-512-64", fixed_ranked, dataset, hierarchy=None,
        ),
        "cross-encoder-requirements-q2-512-64": _prepare_rows(
            "requirements-512-64", fixed_ranked_q2, dataset, hierarchy=None,
        ),
        "cross-encoder-requirements-dynamic-parent": _prepare_rows(
            "requirements-dynamic-parent", hierarchy_ranked, dataset, hierarchy=hierarchy,
        ),
        "cross-encoder-requirements-q2-dynamic-parent": _prepare_rows(
            "requirements-dynamic-parent", hierarchy_ranked_q2, dataset,
            hierarchy=hierarchy,
        ),
    }
    summaries = {
        key: _summary(key, rows) for key, rows in rows_by_method.items()
    }
    llm_summary = (
        dict(llm_report.get("summaries") or {}) if llm_report is not None else {}
    )
    reference = llm_summary.get("baseline-512-64")
    gates = {}
    harmful: dict[str, float] = {}
    if reference:
        llm_rows = {
            str(row["case_id"]): row
            for row in (llm_report or {}).get("rows", {}).get("baseline-512-64", ())
        }
        for method, summary in summaries.items():
            rows = rows_by_method[method]
            harmful[method] = statistics.fmean(
                float(
                    row["context_recall"]
                    < float(llm_rows[row["case"].case_id]["packed_recall"])
                )
                for row in rows
            ) if rows and llm_rows else 0.0
            gates[method] = {
                "selected_recall_noninferior_1pp": (
                    summary["selected_evidence_recall_at_5"]
                    >= reference["selected_evidence_recall_at_5"] - 0.01
                ),
                "packed_recall_noninferior_1pp": (
                    summary["packed_evidence_recall"]
                    >= reference["packed_evidence_recall"] - 0.01
                ),
                "multi_condition_improves": (
                    summary["multi_span_packed_completeness"]
                    > reference["multi_span_packed_completeness"]
                ),
                "zero_online_model_tokens": (
                    summary["model_input_tokens"] == 0
                    and summary["model_output_tokens"] == 0
                ),
                "harmful_context_rate_zero": harmful[method] == 0.0,
            }
            gates[method]["passes_quality_and_token_gates"] = all(
                gates[method].values()
            )
    latencies = [
        float(row["rerank_usage"]["latency_ms"]["sum"])
        for rows in rows_by_method.values() for row in rows
    ]
    cascade = []
    if llm_report is not None:
        cascade = _cascade_replay(
            rows_by_method["cross-encoder-baseline-512-64"],
            (llm_report.get("rows") or {}).get("baseline-512-64", ()),
        )
    public_rows = {}
    for method, rows in rows_by_method.items():
        public_rows[method] = [{
            **_public_row(row),
            "cross_encoder_cutoff_margin": row.get("cross_encoder_cutoff_margin"),
        } for row in rows]
    return {
        "schema_version": 1,
        "dataset_id": dataset.manifest["dataset_id"],
        "model": {
            "name": model_name,
            "max_length": 512,
            "device": str(model.device),
            "model_load_ms": model_load_ms,
        },
        "contract": {
            "candidate_capture": "same BM25/Dense/RRF owner as the LLM experiment",
            "simple_selection": "cross-encoder query/chunk score; Top-5",
            "parallel_selection": (
                "compare one or two top-scored anchors per requirement, then fill by "
                "max requirement score; no weighted aggregate"
            ),
            "online_llm_calls": 0,
            "gold_used_by_selection": False,
        },
        "summaries": summaries,
        "llm_reference_summaries": llm_summary,
        "gates_vs_llm_baseline": gates,
        "harmful_context_rate_vs_llm_baseline": harmful,
        "mean_cross_encoder_latency_ms": statistics.fmean(latencies) if latencies else 0.0,
        "low_confidence_cascade_replay": cascade,
        "rows": public_rows,
        "limitations": [
            "The selected MS MARCO MiniLM model is an English efficiency baseline, not a multilingual production decision.",
            "The 12 parallel cases are deterministic composites of existing Doc2Dial turns and remain a stress suite, not natural traffic.",
            "A 1pp point-estimate gate is provisional; heldout paired confidence intervals are required for release.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--query-capture", type=Path, required=True)
    parser.add_argument("--requirement-capture", type=Path, required=True)
    parser.add_argument("--llm-report", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(
        RagDataset.load(args.dataset),
        json.loads(args.query_capture.read_text(encoding="utf-8")),
        json.loads(args.requirement_capture.read_text(encoding="utf-8")),
        model_name=args.model,
        llm_report=(
            json.loads(args.llm_report.read_text(encoding="utf-8"))
            if args.llm_report else None
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "model": report["model"],
        "summaries": report["summaries"],
        "gates_vs_llm_baseline": report["gates_vs_llm_baseline"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
