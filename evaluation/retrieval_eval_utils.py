"""Pure deterministic helpers retained for the local retrieval evaluation suite."""
from __future__ import annotations

import statistics
import time
from typing import Any, Mapping, Sequence


def query_variants(row: Mapping[str, Any], strategy: str) -> list[tuple[str, str, bool]]:
    values = [("raw", str(row["raw_query"]), False)]
    if strategy in {"standalone", "standalone_multi2", "all"}:
        text = str(row.get("standalone") or "").strip()
        if text and text != values[0][1]:
            values.append(("standalone", text, False))
    if strategy in {"multi2", "standalone_multi2", "all"}:
        for index, raw_text in enumerate(row.get("expansions") or ()):
            text = str(raw_text).strip()
            if text and text not in {item[1] for item in values}:
                values.append((f"expansion-{index}", text, False))
    if strategy in {"hyde", "all"}:
        text = str(row.get("hyde") or "").strip()
        if text:
            values.append(("hyde", text, True))
    return values


def query_weights(
    variants: Sequence[tuple[str, str, bool]], *, raw_mass: float,
    lexical_weight: float, vector_weight: float,
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


def round_robin_union(
    rankings: Mapping[str, Sequence[str]], order: Sequence[str], *, limit: int,
) -> list[str]:
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


def select_requirements(
    model, capture: Mapping[str, Any], *, anchors_per_requirement: int = 1,
    final_k: int = 5,
) -> dict[str, Any]:
    if anchors_per_requirement < 1:
        raise ValueError("anchors_per_requirement must be positive")
    started = time.perf_counter()
    rankings: dict[str, list[str]] = {}
    scores: dict[str, dict[str, float]] = {}
    for requirement in capture["requirements"]:
        pairs = [
            (requirement.query, str(capture["hit_by_id"][candidate_id].get("content") or ""))
            for candidate_id in capture["fused_ids"]
        ]
        values = [float(value) for value in model.predict(
            pairs, batch_size=16, show_progress_bar=False,
        )]
        by_id = dict(zip(capture["fused_ids"], values, strict=True))
        rankings[requirement.requirement_id] = sorted(
            capture["fused_ids"], key=lambda value: (-by_id[value], value),
        )
        scores[requirement.requirement_id] = by_id
    selected: list[str] = []
    assignments: dict[str, list[str]] = {}
    for requirement in capture["requirements"]:
        anchors = rankings[requirement.requirement_id][:anchors_per_requirement]
        assignments[requirement.requirement_id] = list(anchors)
        for candidate_id in anchors:
            if candidate_id not in selected and len(selected) < final_k:
                selected.append(candidate_id)
    remaining = sorted(
        (value for value in capture["fused_ids"] if value not in selected),
        key=lambda value: (-max(scores[key][value] for key in scores), value),
    )
    selected.extend(remaining[:max(0, final_k - len(selected))])
    latency_ms = (time.perf_counter() - started) * 1000
    return {
        **capture, "reranked_ids": selected[:final_k],
        "selection_assignments": assignments, "missing_requirement_ids": [],
        "rerank_error": None,
        "rerank_usage": {
            "input_tokens": 0, "output_tokens": 0, "model_calls": 0,
            "latency_ms": {"p50": latency_ms, "p95": latency_ms,
                           "max": latency_ms, "sum": latency_ms},
        },
        "cross_encoder_scores": scores,
    }


def cascade_replay(
    cross_rows: Sequence[Mapping[str, Any]], llm_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    llm_by_case = {str(row["case_id"]): row for row in llm_rows}
    ordered = sorted(cross_rows, key=lambda row: (
        float(row.get("cross_encoder_cutoff_margin", float("inf"))),
        row["case"].case_id,
    ))
    result = []
    for fallback_count in sorted({round(len(ordered) * rate) for rate in (
        0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0,
    )}):
        fallback_ids = {row["case"].case_id for row in ordered[:fallback_count]}
        recalls, latencies = [], []
        llm_input_tokens = llm_output_tokens = 0
        for row in cross_rows:
            case_id = row["case"].case_id
            cross_latency = float(row["rerank_usage"]["latency_ms"]["sum"])
            if case_id in fallback_ids:
                llm = llm_by_case[case_id]
                recalls.append(float(llm["packed_recall"]))
                usage = llm["selection_usage"]
                llm_input_tokens += int(usage.get("input_tokens", 0))
                llm_output_tokens += int(usage.get("output_tokens", 0))
                latencies.append(cross_latency + float(usage["latency_ms"]["sum"]))
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
    ordered = sorted(map(float, values))
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction
