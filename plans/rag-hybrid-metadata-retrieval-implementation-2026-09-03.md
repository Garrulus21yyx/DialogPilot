# RAG Hybrid + Metadata Retrieval Implementation — 2026-09-03

## Goal

Repair first-stage Knowledge retrieval at its owners by giving every child chunk a
non-authoritative retrieval representation (`title + section path + content`),
indexing that representation for both dense and lexical search, and supporting
metadata-scoped hybrid retrieval with a global fallback. Preserve original source
text and source offsets as the only evidence authority. Add up to two constrained
query expansions as non-evidence retrieval routes while retaining Raw and
Standalone routes.

## Positive contract

- Ingestion derives deterministic retrieval text without changing source content
  or source-coordinate provenance.
- Dense and lexical indexes consume the same retrieval representation and bind its
  version/fingerprint into the retrieval generation.
- Source metadata that contributes to retrieval text participates in v1 revision
  identity, so a title/type/applicability change cannot silently reuse stale text.
- Security/version metadata remains a mandatory hard filter.
- Optional semantic metadata creates an additional scoped retrieval route; it may
  boost results but cannot eliminate the global fallback route.
- Candidate fusion deduplicates by authoritative child candidate ID and records
  source ranks for every query × retrieval × scope route.
- Production query transformation emits Raw, usable Standalone, and at most two
  constrained expansions; missing/invalid expansions fail back to the remaining
  variants and are observable in the trace.
- Existing callers without section/metadata fields retain valid behavior.
- Tests prove retrieval-text construction, source/evidence separation, scoped-route
  fallback, hybrid fusion, and generation identity drift handling.

## Plan

1. **completed** — Audit current schema, ingestion, backend retrieval, migrations,
   runtime composition, and tests; identify the owning types and compatibility
   surface.
2. **completed** — Add the deterministic child retrieval-text contract and integrate
   it into dense and lexical indexing without changing evidence projection.
3. **completed** — Add bounded metadata-scoped retrieval plus mandatory global
   fallback at the PostgreSQL candidate-source owner.
4. **completed** — Update manifests/fingerprints, runtime descriptions, and affected
   callers.
5. **completed** — Add owner-level, integration, and regression tests; run focused and
   adjacent suites.
6. **completed** — Review the completed causal surface and record final evidence.

## Files changed

- `plans/rag-hybrid-metadata-retrieval-implementation-2026-09-03.md` — plan and
  execution ledger.

## Non-goals

- Do not tune production route weights on the consumed 120-case heldout.
- Do not add Parent/window expansion or a CrossEncoder in this change.
- Do not enable HyDE in the production path before a fresh offline ablation proves
  incremental recall without unacceptable precision/cost regression.
- Do not treat generated query text or retrieval metadata as factual evidence.
- Do not use inferred domain as a sole hard filter.

## Verification evidence

- Non-PostgreSQL owner tests: 46 passed before query-expansion integration.
- Existing PostgreSQL retrieval/source/projector tests: 23 passed.
- New and adjacent retrieval tests with live PostgreSQL: 41 passed, including
  contextual text, section ownership, query expansion, BM25, and metadata fallback.
- Final owner/integration/migration suite with live PostgreSQL: 71 passed.
- Adjacent runtime/authority/evaluation suite: 12 passed, 2 environment-gated skipped.
- Python compilation passed for every changed runtime and migration module.
- `git diff --check` passed. Ruff was not available in the project virtualenv.
