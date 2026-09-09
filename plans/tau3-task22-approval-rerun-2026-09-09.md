# Task 22 approval-continuity regression

Status: FAILED business regression (one completed attempt). Runtime HEAD 88bcb85; unrelated dirty worktree changes retained
and captured by the runner's source hashes. No runtime edits during this run.

Scope: one previously failed retail development task, ID22 (train offset17/count1).
Hypothesis: current-input provenance and coherent approval continuation let both
requested address changes complete without preliminary/repeated confirmation.
Prior evidence: tau3-context-interaction-ten-2026-09-09, task22, neither write,
operation-plan mismatch and repeated confirmation followed by withdrawal.

Fixed: existing configured production models, user anthropic/deepseek-v4-flash,
seed300, max_steps80, user_max_tokens512, no completion override, Encoder disabled
as in the earlier baseline. One attempt, existing ENV/ACTION/ALL evaluators only;
no provider switch, retry selection, or extra judge experiment.

Output: artifacts/eval/tau3-task22-approval-continuity-2026-09-09.
Acceptance: inspect both requested business changes, exact proposal/approval and
write ordering, final public answer, repeated confirmations, termination and
official scores separately. A provider failure or unavailable judge is not a
business score of zero. This known development task is not held-out evidence.

## Result

Run: 2026-09-09 03:31:03–03:32:10 UTC; termination `user_stop`.
ENV=0, ACTION=0, ALL=0; evaluation_errors is empty. Both requested address
writes are absent. This is not unavailable scoring or a provider quota failure.

The application found the user and three orders, collected the full new address,
prepared the account-address proposal and asked approval. The user explicitly
approved. Planning conversion then raised `approval_response_requires_hold_only`.
The next withdrawal and follow-up also failed; three generic failure replies
reached the user. No write tool executed. The earlier state/country question was
missing-input collection, not another execution approval; this run does not prove
that duplicate confirmation is eliminated because execution never resumed.

Immediate mechanism: conversation_actions.action_proposal accepts review_action's
optional response only for hold with one tool call. Its tool schema exposes this
field alongside all decisions, so schema acceptance and conversion acceptance
disagree. The logged error establishes a rejected response/decision/batch
combination, but the local error log does not retain the raw model arguments;
do not claim which exact combination was generated. The failure is an application
planning-contract rejection, not evidence that the model failed to understand yes.

A separate public-output defect remains: the first reply exposed drafting prose
("I need to authenticate the user first. Let me ask..."). Both defects reopen
the approval/publication contract; compliant scripted examples were insufficient
closure evidence. No production fix or second paid attempt was made in this run.

Evidence in the output directory: manifest.json (source hashes/configuration),
task-22.json (scores), task-22-trajectory.json (dialogue/tools/target turns), and
application-errors.log (three conversion failures). Failed artifacts are retained.
Next: reconcile the approval decision + accompanying text + additional-action
algebra at its owner and consumers before another runtime modification or rerun.
