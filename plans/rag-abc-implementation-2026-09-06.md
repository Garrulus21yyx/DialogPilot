# RAG A/B/C implementation

## Positive contract
- Shared knowledge_search accepts an Agent-resolved query; runtime supplies authenticated scope and pinned policy/generation. Historical query adaptation is explicit, not a second Agent interpretation.
- Retrieval transport success, valid evidence, requirement satisfaction and supported answers are distinct. Only valid evidence satisfies knowledge.active_source in every executor.
- Model-visible evidence preserves complete selected spans within token budgets; full artifacts retain provenance. Knowledge answers pass the same evidence contract on direct and delegated paths.
- Supported text/Markdown/JSON imports carry applicability and effective versions; updates/withdrawals atomically rebuild authorized projections and invalidate generation-bound caches. Unsupported formats remain typed failures.

## Work
1. implemented A: shared policy validation, runtime identity/policy propagation, domain outcome mapping.
2. implemented B: resolved query, rerank/model visibility, answer evidence contract.
3. in_progress C: metadata import, temporal applicability, withdrawal/update/cache, structure preservation.
4. pending verification: owner properties, integration/real PostgreSQL, adversarial cases, independent fresh-context review.

## Constraints
Preserve pre-existing uncommitted work. No new RAG Agent/Flow architecture, no retrieval quality tuning (D), user explicitly authorized incremental commit/push. Existing consumed heldout remains report-only. Implementation completion is separate from verified closure.

A verification: 73 passed / 5 environment skips in initial selection; live PostgreSQL 27 passed; final owner/cache/context selection 39 passed. Independent review findings fixed: typed malformed outcomes, metadata-weight cache identity, fully resolved policy snapshot. A implementation ready; B/C and combined closure remain open.

B verification: 95 passed, 1 environment skip. Resolved queries skip conversation rewrite; reranker sees full candidates or explicit budget fallback; knowledge model views bypass generic substring truncation; direct answer generation and delegated drafts share citation + semantic verifier gate. C and combined independent review remain open.
