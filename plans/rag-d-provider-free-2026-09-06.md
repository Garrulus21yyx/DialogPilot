# RAG D: provider-free first, measurable full-chain optimization

Baseline: 5b60455. User authorized implementation, local experiments, incremental commit/push. No external inference API calls in this phase. Preserve preexisting workspace changes.

## Positive contract
Each experiment freezes source corpus, source-level evidence labels, case split/consumption, model/index identities, route depth and output budgets. Source parsing, chunking, embedding, query, retrieval, fusion, reranking, packing and actual model-visible serialization have separate measured boundaries. Cached artifacts are reused only when their inputs and versions match. Public regression and explicitly synthetic Chinese ecommerce development cases are present from the beginning. Heldout is never used to select weights. Offline replay is not represented as a live Conversation Agent or final-answer accuracy measurement.

## Work
1. complete: inventory reusable datasets/models/captures, freeze a reproducible provider-free development baseline and stage instrumentation.
2. in_progress: run deterministic parsing/chunk/provenance/serialization diagnostics, local retrieval and fusion replay; diagnose losses and select bounded fixes.
3. implemented and verified opt-in (production load benchmark pending): implement bounded parallel lexical versus query-embedding+dense retrieval with consistent identity, deadlines, concurrency and typed failures; measure equivalence and latency.
4. in_progress: compare fixed, corpus-type and lightweight query-adaptive fusion on development data only; add hierarchy/chunk/model variants only where failures justify them.
5. pending: provider-free regression/heldout acceptance and independent review, report wins/regressions/costs; commit/push each verified coherent batch.
6. pending external phase: production-provider query/generation validation only after an explicit measured budget is established. No final-answer gains claimed from local retrieval metrics.

## Exit criteria
Reproducible commands and checksums, per-case stage evidence, paired improvements and harms, resource/latency measurements, valid split provenance, and limitations must agree. Do not sum overlapping test selections or conflate pipeline replay with live Agent execution.

## 2026-09-06 development checkpoint
20 calibration cases, then actual 56 development cases (36 public groups +20 synthetic;55 total groups). Local CrossEncoder +2/56 at current weights, no final-answer measurement. Smaller chunks +3/56 with one harm; adaptive fusion grouped CV ties fixed at44/56. PG microbenchmark parallel slower, so opt-in only. Root diagnostic: 5/6 candidate failures already have gold parent in first5. See docs/rag-d-provider-free-2026-09-06.zh-CN.md. Full D not closed.
