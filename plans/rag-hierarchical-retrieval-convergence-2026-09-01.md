# RAG Hierarchical Retrieval Convergence

## Goal

Replace the evaluation-only hand-built parent-child topology with mature
hierarchical splitting/auto-merge components, then compare five bounded
retrieval/packing contracts on the same frozen customer-support dataset before
promoting any implementation into the production `KnowledgeBase` path.

## Root-cause model

The current experiment truncates reranked children to five before mapping and
deduplicating parents, does not aggregate sibling evidence at parent level, and
greedily packs indivisible 1024-token parents into a 2600-token budget. This
reduces the delivered contexts from five baseline chunks to a mean of 2.28
parents and loses multi-condition evidence. The experiment therefore tests a
specific fixed expansion policy, not hierarchical retrieval as a whole.

## Constraints

- Reuse a maintained hierarchical splitter and auto-merger; do not duplicate
  their tree/threshold algorithms in DialogPilot.
- Keep source IDs, checksums, original character offsets, public scope,
  EvidencePack projection, model policy, and evaluation metrics authoritative in
  DialogPilot.
- Do not replace the production Chroma/BM25 index until the candidate passes Dev.
- Do not use Gold evidence during online selection or packing. Gold is scorer-only.
- Compare all candidates under the same query capture, hybrid weights, reranker,
  final token budget, generator, and Judge contract.

## Candidate contracts

1. `baseline-512`: current fixed 512/64.
2. `fixed-parent-child-256-1024`: historical implementation, retained as witness.
3. `unique-parent-aggregation`: child Top-20; aggregate parent score from max
   child score plus sibling support; deduplicate before parent Top-K.
4. `dynamic-auto-merge`: mature AutoMergingRetriever; multiple sibling hits may
   promote to a parent, isolated hits remain leaf/window evidence.
5. `budget-aware-mixed`: allow 256 leaf, 512 middle/window, and 1024 parent;
   maximize marginal query-relevance evidence under the token budget, use token
   cost as a tie-break, and fall back to smaller descendants when a parent does
   not fit. A pure score/token ratio was falsified because it filled the five
   context slots with weak leaves while leaving roughly half the budget unused.

## Steps

1. **done** — Selected Haystack 3.1 `HierarchicalDocumentSplitter` and
   `AutoMergingRetriever`; confined them to evaluation/development dependencies.
2. **done** — Added the hierarchical adapter and stable source-span projection.
3. **done** — Implemented parent aggregation and budget-aware mixed selection at
   DialogPilot-owned ranking/packing boundaries without Gold leakage.
4. **done** — Added invariant tests for unique-parent backfill, dynamic merge,
   source offsets, budget fallback, determinism, and no Gold access.
5. **done** — Ran structural and rerank+packing gates; rejected dominated candidates before
   paid rerank/generation.
6. **done** — No candidate survived the multi-condition/harmful gates, so generation/Judge
   was intentionally not run; updated the audit, architecture, project pitch,
   interview guide, evaluation page, and sanitized report.

## Files

- `plans/rag-hierarchical-retrieval-convergence-2026-09-01.md` — this plan.
- `requirements-rag-eval.txt` — isolated mature hierarchical-retrieval dependency.
- `requirements-dev.txt` — CI coverage for the evaluation adapter.

## Verification record

- `40 passed` across hierarchical adapter, RAG pipeline, and topology tests.
- Haystack source projection was checked for every nested node against the
  original source slice.
- Long Doc2Dial Dev rerank+packing: dynamic auto-merge packed recall `0.7500`,
  multi-condition completeness `0.6875`, harmful rate `0.0833`; it did not pass.
- Rerank short aliases reduced the hierarchical batch failure rate `4/36→0/36`
  and output tokens `37,477→4,500` while mapping back to stable IDs.
