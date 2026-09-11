# RAG pipeline evaluation implementation plan

## Goal

Build a small, reproducible, customer-support-oriented RAG evaluation harness that attributes quality and cost to preprocessing/chunking, query transformation, first-stage retrieval, fusion/reranking, context packing, and generation. Configuration selection must be Dev-only and explainable from recorded metrics rather than fixed folklore values.

## Constraints and invariants

- Preserve the user's existing dirty worktree and avoid overlapping unrelated edits.
- Keep source-document identity and evidence spans authoritative; chunk IDs are configuration-specific projections.
- Never use heldout results to select configuration.
- Record every stage's candidates, evidence retention, latency, and configuration fingerprint.
- Treat exact entities, negation, unsupported questions, and RAG-vs-tool routing as named slices.
- Prefer deterministic metrics; LLM judges are secondary and must be calibrated against reviewed examples.
- Start with bounded local fixtures and adapters; do not commit large third-party corpora.

## Steps

1. **done** — Define the pipeline configuration, evidence-span dataset contract, stage trace, and explainable selection contract.
2. **done** — Implement structure-aware/fixed chunk candidates and evidence-containment metrics.
3. **done** — Implement configurable raw/multi-query/HyDE inputs, capture-once query replay, stable-ID fusion, and production-shared listwise reranking.
4. **done** — Implement provenance-aware context packing, grounded generation, citation validation, deterministic metrics, and secondary LLM judgement.
5. **done** — Add a bounded, domain-balanced Doc2Dial customer-support adapter and versioned local artifact. TechQA/QReCC are explicit future extensions, not required for this bounded experiment.
6. **done** — Add unit/integration tests for positive invariants and run focused regression tests.
7. **done** — Document experiment order, candidate grids, Dev-only paired selection, and commands.

## Produced/modified files

- `plans/rag-pipeline-evaluation-2026-08-31.md` — this plan.
- `mcp/document_chunker.py` — authoritative source-offset-preserving chunk owner.
- `mcp/knowledge_base.py` — production ingestion delegates to the chunk owner and projects provenance.
- `evaluation/rag_pipeline/` — contracts, versioned dataset loader, metrics, fusion, selection.
- `evaluation/rag_chunk_ablation.py` — preprocessing ablation CLI.
- `evaluation/rag_retrieval_ablation.py` — capture-once BM25/vector and offline RRF replay CLI.
- `evaluation/rag_query_capture.py` / `rag_query_ablation.py` — bounded online capture and offline query-strategy replay.
- `evaluation/rag_rerank_ablation.py` — listwise rerank capture, retry-resume, and evidence-loss gates.
- `evaluation/rag_packing_ablation.py` / `rag_generation_evaluation.py` — context and answer-stage evaluation.
- `mcp/query_transformer.py`, `result_reranker.py`, `context_packer.py`, `grounded_answer_generator.py` — production/evaluation shared owners.
- `scripts/build_doc2dial_rag_subset.py` — bounded official Doc2Dial adapter.
- `tests/test_rag_pipeline_evaluation.py` — stage/property tests.
- `tests/test_knowledge_base_retrieval.py` — production provenance regression assertions.
- `artifacts/eval/doc2dial-rag-mini-dev-v1/` — generated 100-document/300-case Dev dataset and reports.
- `docs/rag-pipeline-evaluation.zh-CN.md` — experiment contract, commands, evidence, and current bounded conclusions.

## Verification record

- Baseline: `PYTHONPATH=. .venv/bin/pytest tests/test_knowledge_base_retrieval.py tests/test_retrieval_ablation.py tests/test_layered_eval_dataset.py -q` — 33 passed.
- Focused after implementation: 37 passed across new and existing retrieval suites.
- Found and repaired source-offset drift caused by trimming a source document before chunking; prior exploratory numbers were invalidated and rerun after the owner repair.
- Doc2Dial Dev chunk projection after repair: fixed 512/64 preserved 100% of 488 evidence spans with 314 chunks.
- Real retrieval, 300 cases: fixed 512/64 + BM25 0.75 / dense 0.25 / RRF k=10 achieved Evidence Recall@20 0.6244, MRR 0.4054, nDCG@20 0.4596.
- Comparative retrieval after repair: fixed 256/32 Evidence Recall@20 0.5622; fixed 384/48 0.5944; fixed 512/64 0.6244.
- Recommended fusion vs BM25-only Evidence Recall delta +0.0606, group-paired bootstrap 95% CI [+0.0319, +0.0911].
- Chroma emits a non-fatal anonymous telemetry signature warning in this installed dependency combination; experiment completion and result files are unaffected.
- Full repository verification after the first implementation increment: 293 passed.
- Query pressure set: Raw 25% + Standalone 75% achieved Recall@20 0.7708 vs Raw 0.6667; paired 95% CI for delta [+0.0208, +0.1875].
- LLM listwise rerank achieved Recall@5 0.7500 vs 0.5938 without rerank; final typed fallback 0/48 after accepting validated bare ID arrays.
- Top-5 / 2600 tokens was the smallest tested packing contract that preserved reranked evidence recall 0.7500.
- Grounded generation v3 passed the predeclared Dev gates: language match 1.0, evidence citation recall 0.9167 when evidence was present, judge grounded 0.9792, generator/judge failures 0/48.
- Full repository verification after the completed pipeline increment: 307 passed in 7.43s.
- This is implementation completion on Dev, not verified closure; Heldout, human judge calibration, sequential latency, and online shadow/canary remain required.
