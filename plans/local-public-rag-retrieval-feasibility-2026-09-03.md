# Local Public RAG Retrieval Feasibility — 2026-09-03

## Goal

Produce interview-defensible evidence that a fully local first-stage retrieval
pipeline improves evidence recall on public, reproducible document-grounded
conversation data. No proprietary business data and no external inference API
may be required for the candidate pipeline.

## Frozen evaluation contract

- Development/tuning data: public Doc2Dial train/dev conversations, grouped by
  conversation and disjoint from every consumed test cohort.
- Primary untouched validation: a newly sampled remainder of Doc2Dial official
  test, excluding every previously consumed group checksum.
- Cross-dataset evidence: public MTRAG human retrieval tasks, reported separately
  without tuning on MTRAG evaluation outcomes.
- Primary metric: all-evidence Candidate Recall@20; also report Recall@5/50,
  document recall, MRR/nDCG, latency, index size, and domain/document-length
  slices.
- Baseline: current local BGE-M3 dense + PostgreSQL BM25 weighted RRF using raw
  and deterministic history-aware queries; no LLM rewrite.
- Candidate stages: BGE-M3 learned sparse, BGE-M3 token-level late interaction,
  and a separately measured local cross-encoder. Candidate pool depth is an
  efficiency parameter, not the claimed source of quality improvement.
- Configuration selection reads Dev only. Heldout and MTRAG runs are report-only.

## Plan

1. **completed** — Audit local model artifacts/runtime and public dataset
   licenses, schemas, official splits, and existing consumed-group exclusions.
2. **completed** — Add deterministic public dataset adapters for sufficiently
   sized Doc2Dial Dev and fresh official-test heldout. MTRAG is deferred because
   the requested additional proof cohort was explicitly Doc2Dial.
3. **completed** — Implement a local capture-once retrieval evaluator with no
   external model calls and immutable model/data fingerprints.
4. **completed** — Add BGE-M3 learned-sparse and late-interaction candidate paths;
   verify query/document preprocessing symmetry and stable source coordinates.
5. **completed** — Run paired Dev ablations against the current dense+BM25
   baseline and select one fixed candidate using the declared metrics.
6. **completed** — Add one local cross-encoder comparison over the frozen candidate
   capture; keep first-stage and rerank gains separately attributable.
7. **completed** — Freeze the winning bundle and run a fresh 60-case Doc2Dial
   official-test heldout once in report-only mode.
8. **completed** — Publish checksum-bound reports, reproducible commands, hardware
   latency/cost, failures, and a concise interview narrative.

## Initial evidence

- Current public corpus: 488 Doc2Dial documents and 1,469 child chunks.
- Fixed 512/64 chunk projection contains all 270 gold spans in the two consumed
  cohorts; observed Candidate failure is not an ingestion-containment failure.
- Current Candidate Recall@20 is 125/156. Read-only diagnosis reaches 133/156 at
  fused Top-50, 139/156 at fused Top-100, and 143/156 in the per-route Top-100
  union. This motivates stronger scoring/fusion, not unbounded K.
- Local host: RTX 3080 10GB; CUDA available; BGE-M3 dense artifact present.
- Local runtime now pins `FlagEmbedding==1.3.5`; BGE-M3 and
  BGE-reranker-v2-m3 artifacts are present and weight-hashed.

## Final evidence

- Installed and pinned `FlagEmbedding==1.3.5`; local BGE-M3 and
  BGE-reranker-v2-m3 weight hashes are recorded in every report.
- On 300 public Dev cases, deterministic user-history projection plus
  Dense+Sparse+BM25 raised strict all-evidence Recall@20 from the raw-query
  Dense+BM25 baseline's 72.0% to 98.3%. Local reranking raised strict Recall@5
  from 88.7% to 94.3%.
- On the fresh 60-case official-test heldout over all 488 documents, the fixed
  configuration achieved 93.3% strict Recall@20 and 85.0% Recall@5. The local
  reranker had zero net Recall@5 gain (2 rescued, 2 harmed), so its Dev gain is
  not asserted as stable generalization.
- All 109 heldout evidence spans are contained by the 512/64 chunk projection.
  Of four Candidate misses, one is unresolved dialogue deixis and three hit the
  correct document at rank 1 but miss the evidence child, motivating bounded
  document-gated child retrieval rather than unbounded global K.

## Files changed

- `plans/local-public-rag-retrieval-feasibility-2026-09-03.md` — protocol and
  execution ledger.
- `evaluation/local_bge_m3_retrieval_eval.py` — local BM25, BGE-M3 dense/sparse,
  fusion, rerank, strict metrics, and per-case diagnostics.
- `scripts/run_local_bge_m3_retrieval_ablation.py` — checksum-bound local runner
  with explicit Dev vs report-only roles.
- `tests/test_local_bge_m3_retrieval_eval.py` — deterministic evaluator tests.
- `docs/local-public-rag-retrieval-evaluation-2026-09-03.zh-CN.md` — final
  interview-ready report.
