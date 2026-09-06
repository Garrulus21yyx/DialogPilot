#!/usr/bin/env python3
"""Freeze local RAG development captures and replay fixed fusion without API calls."""

from __future__ import annotations
import os

for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
    os.environ[key] = "1"
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from evaluation.rag_provider_free import (
    sha,
    file_sha,
    select_dev,
    projection,
    replay,
    summarize,
    bm25_matrix,
    deterministic_query,
)
from evaluation.rag_ecommerce_dev import synthetic_development
from evaluation.rag_pipeline.dataset import RagDataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reranker", type=Path)
    parser.add_argument(
        "--candidate-policy", choices=("flat", "parent_child"), default="flat"
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--public-cases", type=int, default=10)
    parser.add_argument("--synthetic-cases", type=int, default=10)
    parser.add_argument("--chunk-tokens", type=int, default=512)
    parser.add_argument("--overlap", type=int, default=64)
    parser.add_argument(
        "--strategy",
        choices=("fixed_tokens", "structure_aware"),
        default="structure_aware",
    )
    parser.add_argument(
        "--query-mode", choices=("raw", "history", "user_history"), default="history"
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output must be new")
    dataset = RagDataset.load(args.dataset)
    synthetic_docs, synthetic_cases = synthetic_development()
    public = select_dev(dataset, args.public_cases)
    cases = public + synthetic_cases[: args.synthetic_cases]
    if any(not c.answerable or c.split != "dev" for c in cases):
        raise ValueError("this diagnostic requires answerable development cases")
    documents = dataset.documents + synthetic_docs
    metrics, chunks, texts = projection(
        documents, cases, args.chunk_tokens, args.overlap, args.strategy
    )
    queries = tuple(deterministic_query(case, args.query_mode) for case in cases)
    files = sorted(
        p
        for p in args.model.iterdir()
        if p.is_file()
        and p.suffix in {".bin", ".safetensors", ".json", ".model", ".pt"}
    )
    model_identity = {p.name: file_sha(p) for p in files}
    identity = {
        "model_files": model_identity,
        "model_path": str(args.model.resolve()),
        "encoding": "bge-m3-dense-full-v1",
        "max_length": 8192,
        "fp16": True,
    }
    args.cache.mkdir(parents=True, exist_ok=True)
    # Disable network even if a library disregards offline environment settings.
    import socket

    def denied(*args, **kwargs):
        raise RuntimeError("network disabled for provider-free evaluation")

    socket.socket.connect = denied
    import torch
    from FlagEmbedding import BGEM3FlagModel
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(args.model), local_files_only=True)
    lengths = [len(tokenizer.encode(text)) for text in texts]
    qlengths = [len(tokenizer.encode(query)) for query in queries]
    if max(lengths + qlengths) > 8192:
        raise ValueError("embedding budget would truncate evidence/query")
    model = None
    encodings = {}
    cache_hits = []
    timings = {}
    for kind, values in [("documents", texts), ("queries", queries)]:
        cache_key = sha({"identity": identity, "texts": values})
        path = args.cache / (cache_key + ".npy")
        started = time.perf_counter()
        if path.exists():
            encodings[kind] = np.load(path, allow_pickle=False)
            cache_hits.append(kind)
        else:
            if model is None:
                model = BGEM3FlagModel(
                    str(args.model), use_fp16=True, devices="cuda:0", batch_size=2
                )
            encodings[kind] = np.asarray(
                model.encode(
                    list(values),
                    batch_size=2,
                    max_length=8192,
                    return_dense=True,
                    return_sparse=False,
                    return_colbert_vecs=False,
                )["dense_vecs"]
            )
            np.save(path, encodings[kind], allow_pickle=False)
        timings[kind + "_encoding_or_cache_ms"] = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    dense = encodings["queries"] @ encodings["documents"].T
    lexical = bm25_matrix(queries, texts)
    timings["exact_dense_and_python_bm25_ms"] = (time.perf_counter() - started) * 1000
    rows = replay(
        documents,
        cases,
        chunks,
        texts,
        queries,
        dense,
        lexical,
        candidate_policy=args.candidate_policy,
    )
    rerank_identity = None
    rerank_ms = 0
    reranked_rows = []
    if args.reranker:
        from FlagEmbedding import FlagReranker

        rerank_identity = {
            p.name: file_sha(p) for p in sorted(args.reranker.iterdir()) if p.is_file()
        }
        locations = sorted(
            {
                (i, cid)
                for i, case in enumerate(cases)
                for row in rows
                if row["case_id"] == case.case_id
                for cid in row["fused"]
            }
        )
        positions = {c.chunk_id: i for i, c in enumerate(chunks)}
        pairs = [(queries[i], texts[positions[cid]]) for i, cid in locations]
        rerank_tokenizer = AutoTokenizer.from_pretrained(
            str(args.reranker), local_files_only=True
        )
        if any(
            len(rerank_tokenizer.encode(q, add_special_tokens=False)) > 4096
            for q, _ in pairs
        ):
            raise ValueError("reranker query budget would truncate context")
        if any(len(rerank_tokenizer.encode(q, t)) > 8192 for q, t in pairs):
            raise ValueError("reranker input would truncate evidence")
        key = sha(
            {
                "model": rerank_identity,
                "pairs": pairs,
                "max_length": 8192,
                "query_max_length": 4096,
                "fp16": True,
            }
        )
        cache = args.cache / (key + ".npy")
        started = time.perf_counter()
        if cache.exists():
            values = np.load(cache, allow_pickle=False)
            cache_hits.append("reranker")
        else:
            del model
            torch.cuda.empty_cache()
            reranker = FlagReranker(
                str(args.reranker),
                use_fp16=True,
                devices="cuda:0",
                batch_size=2,
                query_max_length=4096,
                max_length=8192,
            )
            values = np.asarray(reranker.compute_score(pairs, batch_size=2))
            np.save(cache, values, allow_pickle=False)
        rerank_ms = (time.perf_counter() - started) * 1000
        rerank_scores = np.full(dense.shape, -np.inf)
        for (i, cid), value in zip(locations, values, strict=True):
            rerank_scores[i, positions[cid]] = value
        reranked_rows = replay(
            documents,
            cases,
            chunks,
            texts,
            queries,
            dense,
            lexical,
            rerank_scores=rerank_scores,
            candidate_policy=args.candidate_policy,
        )
    report = {
        "schema_version": "rag-provider-free-v1",
        "baseline_commit": "5b60455",
        "scope": "LOCAL_PIPELINE_REPLAY_NOT_LIVE_AGENT",
        "configuration_selection_allowed": True,
        "external_inference_api_calls": 0,
        "network_disabled": True,
        "public_cases": len(public),
        "synthetic_cases": len(cases) - len(public),
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "input_dataset_manifest": dict(dataset.manifest),
        "synthetic_fixture_sha256": file_sha(ROOT / "evaluation/rag_ecommerce_dev.py"),
        "model_identity": identity,
        "query_mode": args.query_mode,
        "chunk_strategy": args.strategy,
        "chunk_tokens": args.chunk_tokens,
        "overlap": args.overlap,
        "source_k": 20,
        "candidate_policy": args.candidate_policy,
        "parent_limit": 3 if args.candidate_policy == "parent_child" else 0,
        "final_k": 5,
        "context_tokens": 2600,
        "context_budget_scope": "evidence text; complete ToolMessage tokens measured separately",
        "embedding_max_observed_tokens": max(lengths),
        "query_max_observed_tokens": max(qlengths),
        "local_reranker_identity": rerank_identity,
        "rerank_ms": rerank_ms,
        "reranked_metrics": summarize(reranked_rows),
        "implementation_sha256": {
            str(p.relative_to(ROOT)): file_sha(p)
            for p in [
                Path(__file__),
                ROOT / "evaluation/rag_provider_free.py",
                ROOT / "mcp/context_packer.py",
                ROOT / "mcp/tool_manager.py",
                ROOT / "mcp/document_chunker.py",
                ROOT / "mcp/rank_fusion.py",
            ]
        },
        "cache_hits": cache_hits,
        "timings": timings,
        "projection": metrics,
        "metrics": summarize(rows),
        "unmeasured": [
            "live_agent_query_rewrite",
            "production_embedding",
            "postgres_ann_latency",
            "reranker",
            "final_answer",
            "business_tool_routing",
        ],
        "status": "DEVELOPMENT_DIAGNOSTIC",
    }
    args.output.mkdir(parents=True)
    (args.output / "cases.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        )
    )
    (args.output / "reranked-cases.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in reranked_rows
        )
    )
    np.savez_compressed(args.output / "scores.npz", dense=dense, bm25=lexical)
    (args.output / "chunks.jsonl").write_text(
        "".join(
            json.dumps(c.__dict__, ensure_ascii=False, sort_keys=True) + "\n"
            for c in chunks
        )
    )
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "manifest.json").write_text(
        json.dumps(
            {p.name: file_sha(p) for p in args.output.iterdir() if p.is_file()},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "projection": metrics,
                "metrics": report["metrics"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
