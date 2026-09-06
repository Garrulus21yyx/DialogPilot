"""Local model diagnostics using production chunk/fusion/packing/wire owners.

This is explicit pipeline replay, not a live Conversation Agent or answer judge.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
from evaluation.local_bge_m3_retrieval_eval import bm25_matrix, deterministic_query
from evaluation.rag_pipeline.metrics import (
    evaluate_chunk_projection,
    chunk_contains_evidence,
)
from application.knowledge_retrieval_text import build_child_retrieval_text
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.document_chunker import DocumentChunker
from mcp.evidence_pack import EvidencePack
from mcp.rank_fusion import fuse_rankings
from mcp.tool_manager import MCPToolManager, ToolResult
from core.token_estimator import TokenEstimator

WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)


def sha(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def select_dev(dataset, limit):
    # One conversation per group; no heldout rows can enter parameter selection.
    groups = {}
    for case in dataset.cases:
        if case.split == "dev":
            groups.setdefault(case.group_id, []).append(case)
    chosen = []
    for group in sorted(groups, key=sha)[:limit]:
        chosen.append(max(groups[group], key=lambda c: (len(c.history), c.case_id)))
    if not chosen:
        raise ValueError("no development conversations available")
    return tuple(chosen)


def projection(documents, cases, size, overlap, strategy):
    metrics, chunks = evaluate_chunk_projection(
        documents, cases, max_tokens=size, overlap_tokens=overlap, strategy=strategy
    )
    docs = {doc.document_id: doc for doc in documents}
    texts = [
        build_child_retrieval_text(
            title=docs[c.document_id].title,
            section_path=DocumentChunker.section_path_at(
                docs[c.document_id].content, c.start_char, strategy=strategy,
                source_type=str(docs[c.document_id].metadata.get("source_type", "text"))
            ),
            content=c.content,
        )
        for c in chunks
    ]
    return metrics, chunks, texts


def complete(case, ids, by_id):
    if not case.answerable:
        return None
    return all(
        any(chunk_contains_evidence(by_id[cid], span) for cid in ids)
        for span in case.evidence
    )


def ranked(scores, ids, depth):
    return tuple(
        ids[i]
        for i in sorted(range(len(ids)), key=lambda i: (-float(scores[i]), ids[i]))[
            :depth
        ]
    )


def parent_child_candidates(
    fused, routes, by_id, dense_scores, lexical_scores, ids, weight, budget
):
    """Reserve half the global pool and refill from at most three parents.

    Parent ranks use first child occurrence; this is not parent embedding.
    The extra local pass has a compute cost despite the unchanged final pool.
    """
    weights = {"dense": weight, "bm25": 1 - weight}
    parent_routes = {
        name: list(dict.fromkeys(by_id[cid].document_id for cid in ranking))
        for name, ranking in routes.items()
    }
    parents = fuse_rankings(parent_routes, weights=weights, rrf_k=10, top_k=3)
    positions = {cid: i for i, cid in enumerate(ids)}
    allowed = [cid for cid in ids if by_id[cid].document_id in parents]
    local_routes = {
        "dense": sorted(
            allowed, key=lambda cid: (-float(dense_scores[positions[cid]]), cid)
        )[:budget],
        "bm25": sorted(
            (cid for cid in allowed if lexical_scores[positions[cid]] > 0),
            key=lambda cid: (-float(lexical_scores[positions[cid]]), cid),
        )[:budget],
    }
    local = fuse_rankings(local_routes, weights=weights, rrf_k=10, top_k=budget)
    selected = list(dict.fromkeys([*fused[: max(1, budget // 2)], *local, *fused]))[
        :budget
    ]
    return selected, {
        "parents": parents,
        "local_routes": local_routes,
        "global_reserved": max(1, budget // 2),
    }


def replay(
    documents,
    cases,
    chunks,
    texts,
    queries,
    dense,
    lexical,
    *,
    source_k=20,
    final_k=5,
    context_tokens=2600,
    rerank_scores=None,
    candidate_policy="flat",
    weights=WEIGHTS,
):
    if candidate_policy not in {"flat", "parent_child"}:
        raise ValueError("unsupported candidate policy")
    by_id = {c.chunk_id: c for c in chunks}
    docs = {d.document_id: d for d in documents}
    ids = tuple(by_id)
    positions = {cid: i for i, cid in enumerate(ids)}
    corpus_sha = sha([(d.document_id, d.content) for d in documents])
    results = []
    for i, (case, query) in enumerate(zip(cases, queries, strict=True)):
        routes = {
            "dense": ranked(dense[i], ids, source_k),
            "bm25": tuple(
                cid
                for cid in ranked(lexical[i], ids, len(ids))
                if lexical[i, positions[cid]] > 0
            )[:source_k],
        }
        for weight in weights:
            fused = fuse_rankings(
                routes,
                weights={"dense": weight, "bm25": 1 - weight},
                rrf_k=10,
                top_k=source_k,
            )
            hierarchy = None
            if candidate_policy == "parent_child":
                fused, hierarchy = parent_child_candidates(
                    fused, routes, by_id, dense[i], lexical[i], ids, weight, source_k
                )
            selected = (
                fused
                if rerank_scores is None
                else sorted(
                    fused,
                    key=lambda cid: (-float(rerank_scores[i, positions[cid]]), cid),
                )
            )
            candidates = []
            for rank, cid in enumerate(selected[:final_k], 1):
                c = by_id[cid]
                doc = docs[c.document_id]
                candidates.append(
                    ContextCandidate(
                        cid,
                        c.document_id,
                        c.content,
                        c.start_char,
                        c.end_char,
                        title=doc.title,
                        score=1 / rank,
                        source_type=str(doc.metadata.get("source_type", "text")),
                        source_checksum=hashlib.sha256(
                            doc.content.encode()
                        ).hexdigest(),
                        source_revision="local-"
                        + hashlib.sha256(doc.content.encode()).hexdigest()[:20],
                        index_manifest_fingerprint=corpus_sha,
                    )
                )
            packed = ContextPacker().pack(
                candidates, max_tokens=context_tokens, max_chunks=final_k
            )
            pack = EvidencePack.from_packed(
                query, packed, retrieval_policy={"vector_weight": weight}
            )
            # Invoke the actual wire serializer without constructing an API client.
            rendered = MCPToolManager._render_for_model(
                None,
                ToolResult(
                    True,
                    {"status": "OK", "evidence_pack": pack.to_dict(include_text=True)},
                    "knowledge_search",
                    authority="knowledge.active_source",
                ),
            )
            wire = json.loads(rendered)
            visible = []
            for item in wire.get("evidence", []):
                ref = item["source"]
                if (
                    item["text"]
                    != docs[ref["source_id"]].content[
                        ref["start_char"] : ref["end_char"]
                    ]
                ):
                    raise ValueError("wire evidence changed source")
                visible.extend(
                    cid
                    for cid in packed.chunk_ids
                    if by_id[cid].document_id == ref["source_id"]
                    and by_id[cid].start_char == ref["start_char"]
                    and by_id[cid].end_char == ref["end_char"]
                )
            metrics = {
                "chunk_complete": complete(case, ids, by_id),
                "candidate_complete": complete(case, fused, by_id),
                "selected_complete": complete(case, selected[:final_k], by_id),
                "packed_complete": complete(case, packed.chunk_ids, by_id),
                "tool_message_complete": complete(case, visible, by_id),
            }
            results.append(
                {
                    "case_id": case.case_id,
                    "group_id": case.group_id,
                    "corpus_type": "synthetic_ecommerce"
                    if case.case_id.startswith("synthetic:")
                    else "public_doc2dial",
                    "query": query,
                    "dense_weight": weight,
                    "metrics": metrics,
                    "routes": routes,
                    "fused": fused,
                    "hierarchy": hierarchy,
                    "selected": selected[:final_k],
                    "packed": packed.chunk_ids,
                    "tool_message": wire,
                    "tool_message_tokens": TokenEstimator.estimate(rendered),
                    "unmeasured": [
                        "live_agent_query",
                        "provider_rerank",
                        "answer_correctness",
                    ],
                }
            )
    return results


def summarize(rows):
    summaries = []
    for group in ("all", "public_doc2dial", "synthetic_ecommerce"):
        subset = [r for r in rows if group == "all" or r["corpus_type"] == group]
        base = {r["case_id"]: r for r in subset if r["dense_weight"] == 0.25}
        for weight in WEIGHTS:
            selected = [r for r in subset if r["dense_weight"] == weight]
            if not selected:
                continue
            metric_names = selected[0]["metrics"]
            metrics = {
                key: sum(r["metrics"][key] is True for r in selected) / len(selected)
                for key in metric_names
            }
            rescued = [
                r["case_id"]
                for r in selected
                if r["metrics"]["tool_message_complete"]
                and not base[r["case_id"]]["metrics"]["tool_message_complete"]
            ]
            harmed = [
                r["case_id"]
                for r in selected
                if not r["metrics"]["tool_message_complete"]
                and base[r["case_id"]]["metrics"]["tool_message_complete"]
            ]
            summaries.append(
                {
                    "corpus_type": group,
                    "dense_weight": weight,
                    "case_count": len(selected),
                    **metrics,
                    "rescued": rescued,
                    "harmed": harmed,
                    "delta_pp": 100 * (len(rescued) - len(harmed)) / len(selected),
                }
            )
    return summaries
