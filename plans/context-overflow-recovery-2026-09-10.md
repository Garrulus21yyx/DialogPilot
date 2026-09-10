# Context lifecycle convergence

Status: implementation and deterministic boundary validation complete; business closure open.

## Authorized task20 rerun

2026-09-10: user requested the real rerun. Hypothesis: repaired archive/summary/
model-step recovery lets the original task reach approval and execution without
archive reread thrashing. Fixed train task20, seed 300, max_steps 80, same configured
models, no completion-budget override. One attempt, not heldout. Record tool counts,
repeated reads, context errors, real writes, reply correctness and ALL/ENV/ACTION
separately. Output: artifacts/eval/tau3-task20-context-recovery-2026-09-10.
Current worktree hashes are recorded by the runner; pending observability changes
remain present, so this is a current-chain regression, not an isolated ablation.
Status: completed, normal user_stop. ALL/ENV/ACTION all 1, DB matched. Four items
updated once, gift-card charge $71.96. Trajectory 54 messages versus prior 82;
business calls 22 versus 34; identical-call repetitions 7 versus 19. Remaining
public internal IDs, stiff approval language and response-review revisions are
retained in the report. No production edits or prompt changes during the run.
No recorded context/step terminal error, but this run alone does not prove that
reactive compaction was triggered. See
`artifacts/eval/tau3-task20-context-recovery-2026-09-10/report.md`.

## Causal model

Two distinct failures share an incomplete context lifecycle: planner admission
failed after actions were prepared; a later worker repeatedly read archived pages
whose bodies were removed before progress was summarized. The latter's destructive
clear pass was removed in 738e46f. That does not implement overflow recovery.

Owners: archive owns originals and bounded reads; working-context middleware owns
the message window and summary handoff; provider boundary owns complete request
admission; TaskGraph/approval/receipts retain business authority. Recovery cannot
call a business executor or reinterpret an approval.

## Positive contract

- Fitting current results remain inline. Default archive reads return all content
  within the reader allowance; larger sources have explicit continuation.
- Old messages leave the window through a summary, with source navigation retained.
- Input-overflow recovery retries only the rejected model step, with strictly
  smaller input and a bounded attempt count. Transport/output errors are distinct.
- Summary admission counts its own prompt. Oversized history is summarized in
  complete message groups; an indivisible oversized group fails explicitly.
- Mandatory current input, action scope, receipts and task state are not sacrificed
  to make a request fit. Fixed-overhead overflow is a typed failure.
- Original data and business progress remain available on recovery exhaustion.

## Work

1. [done] Inspect planner, worker, archive, summary and model boundaries.
2. [done] Implement one bounded recovery contract using existing SDK hooks.
3. [done] Generated/invariant tests: strict shrink, bounded calls, paired
   messages, no business replay, fitting reads, oversized summary and fixed input.
4. [done] Review complete diff and exceptional paths, record validation limits.

## Reference basis

Prior inspection of liuup/claude-code-analysis at 7b7b915: separate storage,
micro-compaction, summary and reactive-retry lifecycle. This is a third-party
analysis, not an official supported SDK; reactiveCompact implementation is absent.
Reuse LangChain SummarizationMiddleware safe cutoffs and LangGraph state updates;
do not copy leaked implementation or introduce a second Agent loop.

## Implementation boundaries

`target_model_recovery` classifies local/provider input-capacity errors and permits
at most two recoveries. Every actual retry must have a smaller measured input;
20% is the recovery headroom, not a changed proactive summary threshold. Repeated
provider rejection and no reduction have an explicit exhausted-recovery error.
Transport, malformed output and output-token exhaustion are not input recovery.

Planner budgets the fully rendered request (system, capability schemas, messages,
output reserve). It projects archived bodies, removes already summarized history,
then may summarize older dialogue. Recent exchange, current answer and pending
control state remain verbatim. This request-local projection does not overwrite
Transcript or the persistent summary. Provider rejection uses the same projector.

Worker uses the existing compaction middleware's `awrap_model_call` and native
`ExtendedModelResponse`/`Command`: no tool or graph rerun inside recovery. On
success the recovered input and new model output commit together. On exhaustion
the preceding checkpoint and originals remain; explicit graph resume does not
repeat the completed tool. Summary requests retain their own capacity; reducing
the actor's next-request target does not artificially shrink the summary model.

Archive read defaults no longer impose 2,000-character pagination. The framework
reader allocates up to one quarter of input space after actual system/tool overhead,
counts the returned envelope, and returns the whole fitting result. Explicit
ranges remain bounded; truly larger results carry next_offset. This allocation
is shared when multiple calls are dispatched together (at most half the net
window across that batch). Combined admission remains the model-boundary owner's
job. Archive reads have no artifact to rearchive.

Summary work uses SDK safe group boundaries, up to eight model calls including
merges. An indivisible group exceeding the summary capacity fails before calls;
it is not sliced through a tool transaction. Originals stay outside the window.

Non-tool review/response consumers still require their supporting evidence.
This change does NOT summarize authoritative facts, approval scope, schemas or
receipts to make those inputs fit, nor give a reviewer an unusable archive pointer.
Existing post-model review admission remains; irreducible evidence/contract
overflow is a typed failure, not an automatic business retry. Ordinary schema or
provider outages are not silently relabeled as context recovery.

## Validation so far

- 214 related tests passed with PostgreSQL enabled before adding exhaustion matrix.
- New 16-case suite passes with real PostgreSQL: normal recovery, exhaustion,
  explicit checkpoint resume without repeating preparation, planner current
  approval preservation, whole/paged reads, group summaries, strict shrink and
  error classification. No paid business model run.
- Final staged-only export `/tmp/dialogpilot-overflow-staged-dMWXSq`: **220 passed,
  zero skipped**, PostgreSQL enabled. One existing multiprocessing fork warning.
  Includes the 20-case new recovery/reading suite and prior archive, working-source,
  planner, reviewer, actor, lifecycle and process-exit suites.
- `git diff --cached --check` passes. Unrelated pending trace/monitor changes are
  excluded from this commit. Existing reader-schema/evidence-directory work is
  included because it is the same changed read contract and its acceptance test.
- Delivery: implementation commit `014bf0c` pushed; local HEAD and
  `origin/feat/customer-service-target-architecture` were both verified at that
  commit after push. This final delivery attestation is documentation-only.
- Business closure and real-model summary fidelity remain unproven; do not replace
  failed tau3 evidence with these deterministic model doubles.
