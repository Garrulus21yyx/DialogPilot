"""Deterministic legacy-vs-PostgreSQL Knowledge dark-shadow comparison."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from application.hybrid_retrieval import (
    HybridRetrievalBackend,
    HybridRetrievalRequest,
    HybridRetrievalResult,
    KnowledgeSearchScope,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalStatus,
)


class KnowledgeShadowContractError(ValueError):
    pass


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(
        json.loads(line) for line in path.read_text("utf-8").splitlines()
        if line.strip()
    )


@dataclass(frozen=True)
class FrozenKnowledgeShadow:
    path: Path
    raw: Mapping[str, Any]
    corpus_path: Path
    query_path: Path

    @classmethod
    def load(cls, path: str | Path) -> "FrozenKnowledgeShadow":
        resolved = Path(path).resolve()
        raw = json.loads(resolved.read_text("utf-8"))
        if raw.get("schema_version") != "knowledge-pg-shadow-spec-v1":
            raise KnowledgeShadowContractError("unsupported shadow spec")
        corpus = (resolved.parent / str(raw["corpus"]["path"])).resolve()
        queries = (resolved.parent / str(raw["queries"]["path"])).resolve()
        for label, item, target in (
            ("corpus", raw["corpus"], corpus),
            ("queries", raw["queries"], queries),
        ):
            if file_sha256(target) != item.get("sha256"):
                raise KnowledgeShadowContractError(f"frozen {label} checksum drift")
        if canonical_hash(raw["policy"]) != raw.get("policy_fingerprint"):
            raise KnowledgeShadowContractError("frozen policy fingerprint drift")
        if raw.get("publisher") != "LEGACY_BM25_V1" or raw.get("active_switch") is not False:
            raise KnowledgeShadowContractError("dark shadow cannot change publisher")
        return cls(resolved, raw, corpus, queries)

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.raw)


def _variants(row: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[tuple[str, str, float], ...]:
    raw = str(row.get("query") or "").strip()
    standalone = str(row.get("standalone") or raw).strip()
    if not raw:
        raise KnowledgeShadowContractError("query capture contains an empty query")
    if not standalone or standalone == raw:
        return (("raw", raw, 1.0),)
    raw_weight = float(policy["raw_query_weight"])
    standalone_weight = float(policy["standalone_query_weight"])
    total = raw_weight + standalone_weight
    if total <= 0:
        raise KnowledgeShadowContractError("query variant mass is invalid")
    return (
        ("raw", raw, raw_weight / total),
        ("standalone", standalone, standalone_weight / total),
    )


def _fused(
    captures: Sequence[tuple[str, float, HybridRetrievalResult]],
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    scores: dict[str, float] = {}
    candidates: dict[str, RetrievalCandidate] = {}
    routes: dict[str, dict[str, int]] = {}
    rrf_k = int(policy["rrf_k"])
    for variant, variant_weight, result in captures:
        if result.status is not RetrievalStatus.OK:
            continue
        for route, weight, items in (
            ("dense", float(policy["dense_weight"]), result.dense_candidates),
            ("lexical", float(policy["lexical_weight"]), result.lexical_candidates),
        ):
            for item in items:
                candidates[item.candidate_id] = item
                routes.setdefault(item.candidate_id, {})[f"{variant}:{route}"] = item.rank
                scores[item.candidate_id] = scores.get(item.candidate_id, 0.0) + (
                    variant_weight * weight / (rrf_k + item.rank)
                )
    ordered = sorted(scores, key=lambda item: (-scores[item], item))
    return tuple({
        "candidate_id": candidate_id,
        "source_id": candidates[candidate_id].source_id,
        "source_revision": candidates[candidate_id].source_revision,
        "score": scores[candidate_id],
        "source_ranks": routes[candidate_id],
    } for candidate_id in ordered[:int(policy["final_k"])])


def _backend_row(
    *,
    backend: HybridRetrievalBackend,
    generation: Mapping[str, Any],
    tenant_id: str,
    scope: KnowledgeSearchScope,
    policy_fingerprint: str,
    variants: Sequence[tuple[str, str, float]],
    policy: Mapping[str, Any],
    embed_query: Callable[[str], Sequence[float]],
) -> dict[str, Any]:
    captures = []
    statuses = []
    route_rows = []
    for kind, query, weight in variants:
        result = backend.retrieve(HybridRetrievalRequest(
            tenant_id=tenant_id, corpus=RetrievalCorpus.KNOWLEDGE,
            backend_fingerprint=str(generation["backend_fingerprint"]),
            generation_id=str(generation["generation_id"]),
            policy_fingerprint=policy_fingerprint, query_text=query,
            query_embedding=tuple(map(float, embed_query(query))), scope=scope,
            dense_limit=int(policy["candidate_k"]),
            lexical_limit=int(policy["candidate_k"]), exact_dense=True,
        ))
        captures.append((kind, weight, result))
        statuses.append(result.status.value)
        route_rows.append({
            "variant": kind, "status": result.status.value,
            "detail_code": result.detail_code,
            "dense": [item.candidate_id for item in result.dense_candidates],
            "lexical": [item.candidate_id for item in result.lexical_candidates],
        })
    status = (
        "OK" if any(item == "OK" for item in statuses)
        else statuses[0] if len(set(statuses)) == 1 else "CONFLICT"
    )
    return {
        "status": status,
        "routes": route_rows,
        "fused": list(_fused(captures, policy)),
    }


def run_shadow(
    spec: FrozenKnowledgeShadow,
    *,
    legacy_backend: HybridRetrievalBackend,
    postgres_backend: HybridRetrievalBackend,
    embed_query: Callable[[str], Sequence[float]],
) -> dict[str, Any]:
    raw = spec.raw
    policy = raw["policy"]
    filters = raw["filters"]
    scope = KnowledgeSearchScope(
        str(filters["scope"]), str(filters["locale"]), filters.get("product"),
    )
    rows = []
    for query in read_jsonl(spec.query_path):
        variants = _variants(query, policy)
        common = dict(
            tenant_id=str(filters["tenant_id"]), scope=scope,
            policy_fingerprint=str(raw["policy_fingerprint"]), variants=variants,
            policy=policy, embed_query=embed_query,
        )
        legacy = _backend_row(
            backend=legacy_backend, generation=raw["legacy_generation"], **common,
        )
        postgres = _backend_row(
            backend=postgres_backend, generation=raw["postgres_generation"], **common,
        )
        legacy_sources = [item["source_id"] for item in legacy["fused"]]
        postgres_sources = [item["source_id"] for item in postgres["fused"]]
        expected = query.get("expected_source_id")
        forbidden = set(map(str, query.get("forbidden_source_ids") or ()))
        union = set(legacy_sources) | set(postgres_sources)
        intersection = set(legacy_sources) & set(postgres_sources)
        rows.append({
            "case_id": str(query["case_id"]), "split": str(query["split"]),
            "query": str(query["query"]),
            "variants": [list(item) for item in variants],
            "expected_source_id": expected,
            "forbidden_source_ids": sorted(forbidden),
            "legacy": legacy, "postgres": postgres,
            "status_agreement": legacy["status"] == postgres["status"],
            "candidate_order_exact": legacy_sources == postgres_sources,
            "source_overlap_jaccard": (
                len(intersection) / len(union) if union else 1.0
            ),
            "legacy_expected_hit": expected is None or expected in legacy_sources,
            "postgres_expected_hit": expected is None or expected in postgres_sources,
            "legacy_forbidden_hits": sorted(forbidden.intersection(legacy_sources)),
            "postgres_forbidden_hits": sorted(forbidden.intersection(postgres_sources)),
        })
    count = len(rows)
    report = {
        "schema_version": "knowledge-pg-dark-shadow-report-v1",
        "shadow_id": raw["shadow_id"],
        "generated_at": raw["frozen_at"],
        "spec_path": str(spec.path.relative_to(spec.path.parents[2])),
        "spec_fingerprint": spec.fingerprint,
        "corpus": raw["corpus"], "queries": raw["queries"],
        "filters": filters, "policy": policy,
        "policy_fingerprint": raw["policy_fingerprint"],
        "legacy_generation": raw["legacy_generation"],
        "postgres_generation": raw["postgres_generation"],
        "publisher": "LEGACY_BM25_V1", "active_switch": False,
        "case_count": count,
        "metrics": {
            "status_agreement_rate": sum(row["status_agreement"] for row in rows) / count,
            "candidate_order_exact_rate": sum(row["candidate_order_exact"] for row in rows) / count,
            "mean_source_overlap_jaccard": sum(
                row["source_overlap_jaccard"] for row in rows
            ) / count,
            "legacy_expected_hit_rate": sum(row["legacy_expected_hit"] for row in rows) / count,
            "postgres_expected_hit_rate": sum(row["postgres_expected_hit"] for row in rows) / count,
            "legacy_forbidden_hit_cases": sum(bool(row["legacy_forbidden_hits"]) for row in rows),
            "postgres_forbidden_hit_cases": sum(bool(row["postgres_forbidden_hits"]) for row in rows),
        },
        "decision": {
            "status": "COMPARISON_COMPLETE_NOT_CANARY_AUTHORIZATION",
            "release_authorized": False,
            "reason": (
                "pre-Exit dark shadow only; heldout rows are author-created and "
                "the PG lexical provider requires its own quality/latency gate"
            ),
        },
        "rows": rows,
    }
    return {**report, "report_sha256": canonical_hash(report)}


def write_immutable_report(path: str | Path, report: Mapping[str, Any]) -> None:
    target = Path(path)
    encoded = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n"
    if target.exists() and target.read_text("utf-8") != encoded:
        raise KnowledgeShadowContractError("immutable shadow report already differs")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(encoded, encoding="utf-8")
