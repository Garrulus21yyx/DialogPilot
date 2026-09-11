# Fresh RAG Heldout v2 Run — 2026-09-03

## Goal

Evaluate the newly implemented production-aligned Knowledge retrieval path on
the sealed, mutually disjoint 36-case balanced core and 120-case supplement.
Report evidence-span recall at candidate and final selection boundaries without
using either dataset for configuration selection.

## Constraints

- Do not inspect individual heldout labels before the run.
- Do not tune weights, chunking, query variants, or thresholds on either new set.
- Preserve the consumed v1 report and runner as historical evidence.
- The evaluated path must use contextual child text, Dense + BM25, Raw +
  Standalone + bounded expansions, metadata soft routing when hints exist, the
  configured reranker, and production packing semantics.
- Report balanced core, supplement, and combined metrics separately.

## Plan

1. **completed** — Audit the historical heldout runner, runtime credentials,
   embedding/rewrite/rerank providers, and artifact contracts.
2. **completed** — Add a new production-aligned evaluator without rewriting the
   historical dense-only v1 evaluator.
3. **completed** — Add tests proving stage accounting, dataset acceptance, and
   fail-closed artifact behavior.
4. **completed** — Run the 36-case balanced core and persist outputs. The
   original attempt remains an explicitly invalid Raw + Dense/BM25 fallback
   diagnostic after HTTP 402 failures. A separately identified retry is now
   authorized because a synthetic rewrite/expansion/rerank preflight succeeded;
   the frozen retrieval configuration remained unchanged. The valid retry
   completed with no provider or system errors.
5. **completed** — Run the 120-case supplement exactly once and persist outputs.
6. **completed** — Build a checksum-bound combined report and analyze misses
   without changing production configuration.
7. **completed** — Record verification evidence and final status.

## Files changed

- `plans/rag-fresh-heldout-v2-run-2026-09-03.md` — execution ledger.

## Verification evidence

- New evaluator and adjacent historical evaluator tests: 21 passed.
- BGE-M3 artifact revision `5617a9f...` and weight SHA-256
  `b5e0ce3470ab...` verified; 1,469 contextual chunks indexed.
- Balanced-core retrieval status: 36/36 OK, but model usage recorded 108/108
  errors (72 rewrite/expansion APIStatusError, 36 rerank ModelHTTPError).
- External blocker: DeepSeek Anthropic-compatible endpoint returned HTTP 402
  `Insufficient Balance` on an independent synthetic preflight request.
- Recovery preflight: a fresh synthetic request completed Standalone rewrite,
  two bounded expansions, and structured reranking with no model error. This
  preflight did not read or contain heldout data.
- Valid balanced-core retry: 36/36 retrieval status `OK`, 108/108 model calls
  succeeded, and rerank fallback count was zero. Candidate full-evidence recall
  was 28/36; prepack and packed recall were both 22/36.
- Valid supplement run: 120/120 retrieval status `OK`, 360/360 model calls
  succeeded, and rerank fallback count was zero. Candidate full-evidence recall
  was 97/120; prepack and packed recall were both 80/120.
- Verified combined projection: Candidate 125/156, prepack/packed 102/156;
  31 first-stage misses, 23 Candidate-to-Top-5 losses, and zero packing losses.
- Candidate document recall was 131/156, leaving six cases where the correct
  document but not the complete gold span reached Candidate Top-20.
- Against the fused first-stage order's original Top-5, listwise reranking moved
  complete-evidence hits from 101/156 to 102/156: 13 rescues and 12 harms.
- Offline chunk projection contained all 270 gold spans across both cohorts;
  boundary fragmentation was zero for fixed-512-64. Parent/window expansion was
  explicitly not run and remains separate follow-up work.
- Combined artifact verification and adjacent evaluator/dataset tests: 4 passed.
