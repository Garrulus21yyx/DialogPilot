#!/usr/bin/env python3
"""Run a no-API BGE-M3 first-stage retrieval ablation."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import torch
from FlagEmbedding import BGEM3FlagModel, FlagReranker

from evaluation.local_bge_m3_retrieval_eval import (
    bm25_matrix,
    build_local_chunks,
    deterministic_query,
    evaluate_local_rankings,
    fused_candidate_ids,
    sparse_matrix,
)
from evaluation.rag_pipeline.dataset import RagDataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--query-mode", choices=("raw", "user_history", "history"), default="raw",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--include-colbert", action="store_true")
    parser.add_argument("--reranker", type=Path)
    parser.add_argument(
        "--evaluation-role",
        choices=(
            "PUBLIC_DEV_ABLATION",
            "FRESH_HELDOUT_REPORT_ONLY",
            "CROSS_DATASET_REPORT_ONLY",
        ),
        default="PUBLIC_DEV_ABLATION",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output must not already exist")
    dataset = RagDataset.load(args.dataset)
    chunks = build_local_chunks(dataset)
    queries = tuple(deterministic_query(case, args.query_mode) for case in dataset.cases)
    texts = tuple(chunk.retrieval_text for chunk in chunks)
    model = BGEM3FlagModel(
        str(args.model), use_fp16=True, devices="cuda:0",
        batch_size=args.batch_size, query_max_length=256, passage_max_length=512,
        return_dense=True, return_sparse=True,
        return_colbert_vecs=args.include_colbert,
    )
    started = time.perf_counter()
    document_output = model.encode(
        list(texts), batch_size=args.batch_size, max_length=512,
        return_dense=True, return_sparse=True,
        return_colbert_vecs=args.include_colbert,
    )
    query_output = model.encode(
        list(queries), batch_size=args.batch_size, max_length=256,
        return_dense=True, return_sparse=True,
        return_colbert_vecs=args.include_colbert,
    )
    encoding_latency_ms = (time.perf_counter() - started) * 1000
    dense_scores = np.asarray(query_output["dense_vecs"]) @ np.asarray(
        document_output["dense_vecs"]
    ).T
    learned_sparse_scores = sparse_matrix(
        query_output["lexical_weights"], document_output["lexical_weights"],
    )
    lexical_scores = bm25_matrix(queries, texts)
    cross_encoder_scores = None
    reranker_latency_ms = 0.0
    reranker_sha256 = None
    if args.reranker is not None:
        chunk_ids = tuple(chunk.chunk_id for chunk in chunks)
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        pairs = []
        pair_locations = []
        for query_index, query in enumerate(queries):
            selected = fused_candidate_ids(
                dense_scores[query_index], learned_sparse_scores[query_index],
                lexical_scores[query_index], chunk_ids, top_k=20,
            )
            for chunk_id in selected:
                pairs.append((query, by_id[chunk_id].retrieval_text))
                pair_locations.append((query_index, chunk_ids.index(chunk_id)))
        del model
        torch.cuda.empty_cache()
        reranker = FlagReranker(
            str(args.reranker), use_fp16=True, devices="cuda:0",
            batch_size=args.batch_size, query_max_length=128, max_length=512,
            normalize=True,
        )
        rerank_started = time.perf_counter()
        values = reranker.compute_score(pairs, batch_size=args.batch_size)
        reranker_latency_ms = (time.perf_counter() - rerank_started) * 1000
        cross_encoder_scores = np.full(
            (len(queries), len(chunks)), -np.inf, dtype=np.float32,
        )
        for (query_index, chunk_index), value in zip(pair_locations, values, strict=True):
            cross_encoder_scores[query_index, chunk_index] = float(value)
        reranker_sha256 = _sha(args.reranker / "model.safetensors")
    report = evaluate_local_rankings(
        dataset=dataset, chunks=chunks, queries=queries,
        dense_scores=dense_scores,
        sparse_scores=learned_sparse_scores,
        bm25_scores=lexical_scores,
        query_colbert_vectors=(
            query_output["colbert_vecs"] if args.include_colbert else None
        ),
        document_colbert_vectors=(
            document_output["colbert_vecs"] if args.include_colbert else None
        ),
        cross_encoder_scores=cross_encoder_scores,
    )
    report.update({
        "schema_version": 1,
        "evaluation_role": args.evaluation_role,
        "external_inference_api_calls": 0,
        "query_mode": args.query_mode,
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_cases_sha256": dataset.manifest["cases_sha256"],
        "dataset_corpus_sha256": dataset.manifest["corpus_sha256"],
        "model": "BAAI/bge-m3",
        "model_revision": args.model.name,
        "model_weights_sha256": _sha(args.model / "pytorch_model.bin"),
        "flag_embedding_version": "1.3.5",
        "colbert_enabled": args.include_colbert,
        "reranker": (
            "BAAI/bge-reranker-v2-m3" if args.reranker is not None else None
        ),
        "reranker_weights_sha256": reranker_sha256,
        "reranker_latency_ms": reranker_latency_ms,
        "encoding_latency_ms": encoding_latency_ms,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    args.output.mkdir(parents=True)
    report_path = args.output / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "configuration_selection_allowed": args.evaluation_role == "PUBLIC_DEV_ABLATION",
        "evaluation_role": args.evaluation_role,
        "report_sha256": _sha(report_path),
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(report["metrics"], sort_keys=True))
    return 0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
