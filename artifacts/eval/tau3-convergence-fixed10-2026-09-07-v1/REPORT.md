# Fixed ten-task convergence audit — 2026-09-08

Status: batch finished; convergence **not closed**. No production changes during the batch and no substitutions/restarts/best-of selection.

## Reproduction and scope

Candidate `7f103d7` (runtime implementation `6ec8f31`), official retail train offsets4–13, seed300. Actual IDs: 4,6,7,8,10,11,13,14,15,16. Manifest records code hashes, model/configuration, budgets and dirty-worktree disclosure. These are selected development tasks, not held-out performance. Existing Target application and one retail domain are exercised, not HTTP/SSE or multidomain routing. Original task2/3 results remain separate.

| Task | ALL | ENV | ACTION | Final internal completion | Observed outcome / gap |
|---|---:|---:|---:|---|---|
| 4 | unavailable | 1 | 0 | true | Two order modifications succeed. Unnecessary target reconfirmation and missing explicit first price difference remain in approval wording. |
| 6 | 1 | 1 | 1 | false | Exchange committed and correctly reported; resumed domain fails with CONTEXT_BUDGET_EXCEEDED. |
| 7 | unavailable | unavailable | unavailable | false | Corrected lamp-only proposal is displayed; subsequent approval fails with `approved action objective was superseded`; no successful exchange. |
| 8 | 1 | 1 | 1 | true | Lamp exchange and reported completion agree; preliminary all-items confirmation is mixed with payment-choice collection, then formal approval repeats it. |
| 10 | 1 | 1 | 1 | true | Policy-incompatible refund request ends in recorded human transfer; initial generic input request fails to identify the missing field. |
| 11 | 1 | 1 | 1 | true | Both returns requested, but intermediate verifier rejects a committed first return while second approval is pending. |
| 13 | 1 | 1 | 1 | false | Return committed and correctly reported; resumed domain fails with CONTEXT_BUDGET_EXCEEDED. |
| 14 | 1 | 1 | 1 | true | Both returns requested; vague initial question, intermediate domain failure and false customer wording that the already committed first return is unprocessed remain. |
| 15 | 1 | 1 | 1 | true | Size modification succeeds and final reply/state agree. |
| 16 | 0 | 0 | 0 | false | Two cancellations commit; watch return never executes, repeated ineffective replies, simulation reaches max_steps. |

Seven ALL=1 observations are **not seven verified customer-service closures**. Nine simulations finish, one fails at application boundary. Eight DB checks pass, one fails, one unavailable. Task4 judge unavailability is not reward0; task7 is an application failure, not simulator-empty-message failure. ACTION remains the official independent metric, not removed to improve the headline.

## Evidence-backed causal surfaces

1. **Approval/goal authority:** task7's user changes the target set before approval. The application displays a corrected proposal, then rejects assent because its objective is superseded. The failing owner assertion is in `application/turn_planning.py`; the benchmark adapter reports `Failed` without inventing an alternative response. Inspect proposal replacement, control revision, suspended work and published approval binding together. Do not bypass the stale-objective guard or reinterpret assent as permission for old parameters.
2. **Context lifecycle:** tasks6/13 have successful operation receipts but failed domain continuation. Task6 error stack identifies `ContextCompaction.abefore_model`, the `cleared > available` branch before summarization (28557 versus28072 tokens). This branch measures total edited history, not just the unsummarizable protected suffix. Need establish what is duplicated/protected, budget the actual summary request and maintain call/result pairing and archived originals. Raising the cap or marking receipt presence as whole-goal success is not a repair.
3. **Goal / handback / approval meaning:** task16 has pending watch work yet repeated DOMAIN_OUTCOME_REJECTED and approval wording rejection. Domain failure, a customer-facing proposed action and an executable prepared action are different facts. Trace the goal's tool availability, retained pending action and each input/approval transition before changing prompts. Reply prose cannot create approval state or override a failed execution contract.
4. **Temporal result verification:** tasks11/14 reject claims about an earlier committed operation because a different operation is currently pending. Existing receipt evidence and the pending proposal must retain distinct target/time/phase identities. Re-running a write, refreshing an unrelated order, or weakening all claim checks would mask the shared error.
5. **Acceptance weakness:** task4 approval passes despite incomplete first amount disclosure; a new semantic review does not guarantee complete or minimal interaction. Required information, optional choices and one binding authorization need consistent interpretation across domain/assembly/verifier. Avoid a new field/keyword rule per business.

These are diagnosis boundaries, not yet a claim of one proven explanation for every failure. The original two development passes were insufficient closure evidence. Full relevant-owner review and generated state/transition checks are required before restoring closure.

Independent source review sharpened the common state boundary: `ConversationState.accept_work_items` updates a goal revision without retiring its old PendingApproval. `_allowed_claims` still exposes that old approval as PENDING_ACTION. `TurnRuntime._assemble_response` and ResponseAssembler then conflate **an approval retained in the conversation** with **this turn presenting that approval**. Consequently a side question is forced to re-present approval terms, revised prose can describe parameters not bound to the stored approval, and future assent meets the correctly enforced superseded-objective guard. Task16's repeated pending approval ID through unrelated replies is a witness. Exact old proposal parameters still require original trace inspection; no speculative action identity is asserted here.

The final independent review also identifies two consumers needing the same migration: (a) a new side-question invocation can report task_completed=true while the conversation still has WAITING_APPROVAL work, because retained outcomes cover checkpoint resume but not a newly started plan; (b) domain review infers preparation capability from historical working context even when the current exposed tools are read-only. Clarify completion scope using existing authoritative conversation work, and keep the current capability envelope authoritative. Original planner proposal flags/control binding must be inspected before blaming classification. A task16 lookup of a different user ID is an open identity-binding question, not yet a proven cross-user access finding; include it in the authority audit before closure.

## Next coherent repair order

1. Complete independent fresh-context review of corrected approval and multi-goal continuation; inspect context and temporal verifier consumers locally.
2. Define the single authoritative goal/action/interaction transition contract, including partial success and supersession. Update all affected producers/consumers together; preserve receipts and previous user-visible publications.
3. Repair the proven context admission/compaction boundary with protected-input versus compressible-history properties, not a threshold increase.
4. Verify generated permutations (multiple targets, revisions, old committed/new pending, optional choice, rejected handback, resume) plus PostgreSQL restart boundaries.
5. Freeze the next candidate and revalidate. Preserve this entire failed batch. Use additional fresh/generated cases and an independent review; do not call these now-inspected ten tasks held-out.

No new evaluation framework, fallback execution chain or case-specific production branch is authorized by this report. Current evidence is in each `task-*.json`, its full/partial trajectory, `application-errors.log`, simulator diagnostics and manifest.
