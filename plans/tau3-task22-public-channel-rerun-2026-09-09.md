# Task 22 explicit public-channel rerun

Status: FAILED LIVE VALIDATION. Runtime HEAD 305e001; retain existing dirty worktree and runner
source hashes. No code edits during this run. One attempt, no selective retries.

Hypothesis: the explicit public-response channel removes approval/text conversion
rejection and drafting preamble publication while the existing approval owner
executes the prepared action and continues remaining work.

Known development task22, train offset17/count1, seed300, max_steps80,
user_max_tokens512, existing configured models, Encoder disabled as baseline.
No model/provider/budget override. Previous baseline:
artifacts/eval/tau3-task22-approval-continuity-2026-09-09 (all scores0, no writes).

Output: artifacts/eval/tau3-task22-public-channel-2026-09-09.
Report separately: actual account/order address changes and any user withdrawal,
approval/pending-input continuity, public drafting/false claims, termination,
official ENV/ACTION/ALL and evaluation errors. No claim of generalization from
this known single task. Preserve failures and unavailable scores honestly.

## Observed result

One run, 2026-09-09 03:53:21–03:54:26 UTC, process exit0 (runner completed,
not task success). Official ENV=0, ACTION=0, ALL=0; evaluation_errors={}.
Termination=user_stop after15 application turns. The simulator supplied meaningful
messages and eventually left for another channel; this was not an empty-message
or missing-judge failure.

Only find_user_id_by_name_zip and get_user_details actually ran. Neither account
nor order address was written, and no order detail query ran. No approval stage
was reached, so approval continuity and duplicate-confirmation repair remain
unverified by this attempt.

Eleven turns have top-level planning_invalid_provider_output; application-errors.log
also records planning rejection during internal continuation and composition failure.
Their explicit cause is planning_requires_action: the parsed provider result had
no tool calls, whereas the new public-channel contract requires an action even for
a normal question. Composition encounters the same requirement. Users repeatedly
received the generic failed-turn notice; one partial-delivery reply repeats
"The detailed reply could not be verified." No leaked drafting preamble was
observed, but replacing useful replies with errors is not acceptance of the repair.

## Attribution and remaining uncertainty

This run falsifies live-provider compatibility of the delivered contract. Source
sets tool_choice=any; this alone does not prove the provider received/enforced it.
The immediate mechanism is established, but these logs do not establish whether
transport configuration, provider behavior, or SDK response conversion removed the
expected native action. Inspect correlated request/response evidence before changing
production; do not equate missing parsed tool calls with user-intent failure.
The prior simulated SDK/property tests proved the algebra for supplied action
batches, not that this configured live provider produces those batches reliably.

No production edits or selective retries in this validation. Raw evidence remains
in the output directory (manifest, task-22.json, task-22-trajectory.json,
application-errors.log); this report is the scoped delivery artifact.
Next: reconcile actual SDK request, provider response and parsed action at both
planning and composition boundaries. Keep approval/public-output closure open.
