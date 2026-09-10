# Working-context handoff convergence

Status: implementation verified by deterministic simulation; business closure open.
Scope: delegated working-context admission, archive rereads,
summary, checkpoint/continuation consumers. No paid business rerun in this item.

## Evidence and causal model

Task20 session `tau3-3a44dce6bdea42ea8e4c4cddb74ced1d`: generation
`2044a8d4726ef3cd` sees page 2000 but page 0 replaced by history_ref;
`56f28ad231512722` sees page 0 but page 2000 replaced;
`3a0e12ebf772864f` reverses again. Business reads repeat across failed attempts;
archive reads thrash inside an attempt. Directory persistence fixed navigation,
not the destructive working-set eviction. Tests proved storage, not usable handoff.

Owner: ContextCompaction. Producers: tool persistence and scoped archive readers.
Consumers: actor, post-generation review, native checkpoint and continuation.
Business records remain in archive/tool_observations; summaries do not authorize writes.

## Positive contract and bounded design

- Ingestion archives originals and bounds individually oversized business outputs.
- Ordinary working messages remain intact until summary admission is triggered.
- Old history leaves working messages through SDK summarization of its original
  contents, not a preceding destructive ClearToolUsesEdit pass.
- SDK token-based suffix and call/result pairing remain; latest completed batch
  and pending candidate stay intact. Pinned task and deterministic source index survive.
- Actual input capacity is enforced. Summary failure preserves original state;
  budget exhaustion does not silently delete evidence. No second compression runtime.
- Main context isolation, write authorization, freshness and business routing are unchanged.

## Plan

1. [done] Reconcile dirty tree; inspect actual SDK and current primary references.
2. [done] Reproduce sequential/parallel paging through real admission; specify
   invariants for summary input, retained suffix, repeated checks and resume.
3. [done] Remove destructive pre-summary clearing; migrate config/tests/docs.
4. [done] Run parameterized deterministic simulation and boundary suites; inspect
   resulting model messages, not just token count. Review diff across consumers.
5. [done] Record delivery separately from paid-model/business closure.

## Reference practices (accessed 2026-09-10)

- https://code.claude.com/docs/en/how-claude-code-works : isolated subagent context,
  older-output clearing plus compaction; acknowledges possible compaction thrashing.
  Not evidence that arbitrary three-result clearing preserves a retail comparison.
- https://platform.claude.com/docs/en/build-with-claude/context-editing : clearing
  and semantic compaction are different mechanisms, configurable retention/exclusions.
- https://docs.langchain.com/oss/python/langchain/middleware/built-in#summarization :
  SDK summary trigger/retention, reused here with installed API checked locally.

Inference: for this bounded tool workflow, one semantic history-replacement path
is simpler than adding active-page pinning state or tool-specific eviction rules.
No claim of reproducing private Claude Code implementation or SOTA scores.

## Result and evidence

Removed the 70% destructive clearing pass and its soft_fraction configuration;
kept the existing 85% summary trigger and SDK token-based recent suffix.
No active-page state machine, extra summary model, or changed business step budget.
Exceptional irreducibly oversized native results retain the existing bounded
archive projection; ordinary fitting read pages are not evicted on each check.

`tests/test_working_context_paging.py` simulates eight candidates with two pages,
1/2/4 parallel calls, 1000/2000/4000-character pages, and normal/serialized-resume
execution. Extractive summary double derives every value from its actual input.
An old page may leave the suffix only if its values remain in that summary.
Each of 16 pages is requested once; final comparison equals the full-input oracle.
This proves program-side handoff with a faithful summary, NOT real-model summary quality.

- Old HEAD 17b38d6 module loaded in a separate test process: **18/18 failed** at
  missing values in actual next-model input; production files were not reverted.
- New same 18 scenarios: **18/18 passed**.
- Added 70–85% repeated-admission no-op test: no model call, no archive write,
  no message change. It distinguishes checks from actual compaction.
- Relevant full working-tree suite: **165 passed, zero skipped**, including real
  isolated PostgreSQL archive lifetime/scope, checkpoint/review failure, subprocess
  recovery, continuation, main-context isolation and actual provider input budgets.
  One existing multiprocessing fork deprecation warning.
- `git diff --check` passed. Source search found an obsolete experiment manifest
  soft_fraction description; migrated it rather than leaving a misleading second contract.
- Clean staged-only export `/tmp/dialogpilot-context-staged-PoFv5v`: **163 passed,
  zero skipped**, same existing fork warning. Two additional working-tree tests
  belong to unrelated uncommitted observability/navigation work and were excluded
  from this commit. The seven-file delivery removes more production code than it adds.

Unchanged authorities: tool_observations and raw artifacts remain outside lossy
messages, main/child context mapping remains separate, pending approval remains
authoritative business input, working messages use the same checkpoint serializer.
Existing trace_sink changes in the dirty tree are preserved but are not this repair.

No paid task20 rerun and no revised official score. Old already-cleared checkpoints
are not reconstructed by this change. Remaining validation: fresh real-model task,
summary semantic fidelity/cost, and independent fresh-context closure review.
Do not mark the repeatedly reopened business subsystem closed from these tests.
