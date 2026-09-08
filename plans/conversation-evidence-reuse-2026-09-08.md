# Conversation evidence continuity

Base 8d96122; unrelated user worktree edits remain out of scope. R01 remains open.

## Causal model and positive contract

Two independent issues: regression criteria wrongly equated no retrieval with an
incorrect answer; runtime preserves reply text but drops reusable knowledge packs
at Publication. Missing historical evidence does not prove the historical answer
was unsupported. Conversation can continue without unconditional re-investigation.

Owner flow: ResponseAssembler validated packs -> existing private Publication
verification -> scoped PostgreSQL recent publication read -> existing source
validator -> TurnContext -> same planner and answer assembly. Source validator
owns live authorization; the agent owns question coverage, not a second classifier.
Retain source provenance and original applicability. Unavailable/stale evidence
does not become current authority and does not erase the conversation.

Non-goals: new memory database, new routing/verifier model, broad prompt tuning,
new tool loop, freshness by arbitrary TTL, fabricated backward evidence migration,
paid model runs, changes to dirty archive/framework files.

## Steps

1. done: correct evidence grading, inspect publication/context/assembly/source owners.
2. done: implement private final/interaction evidence continuity and current-source reuse.
3. done implementation verification: reuse/no search, stale/unavailable, scope and publication round-trip;
   independent fresh-context review; document limits and commit/push exact scope.

The prior 12 calls remain immutable. Regrading is not another run or a claim of
new model quality. Exit: authoritative linkage and supported validity behavior
tested through consumers, no new per-turn model or duplicated retrieval route.

Independent review found/fixed SQL alias shadowing and missing author E labels.
Current-source validation permits unrelated generation changes while preserving
exact revision/content/current membership and the existing temporal predicate.
Final report: docs/conversation-evidence-reuse-2026-09-08.zh-CN.md.
Parallel work changed HEAD to3ba6683; its R04/R05 code/docs remain outside this commit.
No paid model calls or fake evidence migration. Known bounded scope is recent
ConversationAgent evidence, not universal domain-loop or long-term evidence reuse.

Final relevant suite427 passed in79.54s with a real, fixture-isolated PostgreSQL
database; no skips. Includes HTTP entry, checkpoint restore, publication/privacy,
current effective revisions, provider payload and reply assembly. No paid models.
Implementation verified within this bounded scope; R01 semantic closure remains open.
Commit/push is the remaining delivery step; exact commit recorded in handoff.

Isolated staged-tree rerun (excluding unrelated user worktree source edits):
427 passed,0 skipped,71.56s. Parallel retrieval delivery advanced HEAD to38d22fd;
only the evidence-maintenance status paragraph was shared, runtime changes remain
separately scoped. Final explicit-path commit follows that HEAD without rewriting it.
