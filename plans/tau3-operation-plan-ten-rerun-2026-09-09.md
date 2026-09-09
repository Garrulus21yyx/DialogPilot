# Fixed ten-task regression after operation-plan integration

Status: INCOMPLETE — provider HTTP402 insufficient balance. User explicitly
authorized rerunning the original ten; the runner finished and preserved all rows.

Gap: R1–R6/R8–R9 structural repairs and R6 operation-plan elicitation; R7 prose
quality remains open. Hypothesis: full-goal local planning and repaired lifecycle
reduce invalid preparation, lost continuation and duplicate confirmation. This
run measures actual behavior, not a guaranteed repair claim.

Data: official retail train offset14/count10: 19,20,21,22,23,24,25,28,29,30.
All are exposed development cases, not held-out. One run each, no replacement or
selective reruns. Base commit 5e70eb6; tracked user edits remain present and runner
manifest hashes source files. Do not edit runtime during the run.

Fixed: tau a2c024725189473d2d7cea3a5cfdbcc67478e41f; seed300; max_steps80;
worker/intent DeepSeek v4 flash, synthesis/verifier v4 pro, reasoning none;
user anthropic/deepseek-v4-flash, max_tokens512; no completion override;
encoder disabled as prior baseline. Existing per-work-item20 model-call bound
and segment rejection bound retained. Ten simulations plus official evaluators;
no additional strategy experiment or model retries outside existing runtime.

Output: artifacts/eval/tau3-operation-plan-ten-2026-09-09.
Report every task's termination, actual calls/receipts, final response and official
ENV/ACTION/ALL separately. Judge OpenAI credential is absent at preflight; preserve
evaluation errors rather than report absent ALL as business0. No alternate judge.
Task19 same-order mixed writes are incompatible; choice before write is correct,
not a requirement that both writes happen. A score alone does not attest goal
coverage (notably25). Run failure/cancellation retains partial results.

Adoption evidence: inspect every row and failed decision, count duplicate asks,
invalid/absent plans and replies, verify no silent missing goals or unsupported
success. A model-produced DAG is not itself proof of feasibility. Publish exact
results regardless of whether the hypothesis is supported.

## Actual result

UTC 2026-09-09 01:15:14–01:21:20. First command failed at import (psycopg absent
in tau venv), before any task/API call or output directory. Actual run used the
existing project site-packages via PYTHONPATH with the tau venv; no dependency or
runtime code changes. Source hashes are in manifest.json.

| Task | Execution result | ENV / ACTION / ALL |
|---|---|---|
|19|Eventually explains same-order incompatibility; user chooses water-bottle return; return tool executes. Earlier repeated confirmations, internal drafting prose and two context-budget errors remain.|1 / 1 / unavailable (judge dependency)|
|20|No modification write. Queries/upgrade proposal followed by context-budget failure; retry does not complete; later citation validation fails. Regression against prior success.|0 / 0 / 0|
|21|Partial conversation and reads/calculations only; no modification tool in saved partial trajectory. Also application budget/citation failures before provider quota ends run.|not evaluated|
|22|First simulator request fails HTTP402; no business assessment.|not evaluated|
|23|First simulator request fails HTTP402; no business assessment.|not evaluated|
|24|First simulator request fails HTTP402; no business assessment.|not evaluated|
|25|First simulator request fails HTTP402; no business assessment.|not evaluated|
|28|First simulator request fails HTTP402; no business assessment.|not evaluated|
|29|First simulator request fails HTTP402; no business assessment.|not evaluated|
|30|First simulator request fails HTTP402; no business assessment.|not evaluated|

The provider reports `Insufficient Balance` (HTTP402) on task21 and subsequent
simulator startup calls. Missing ALL for19 is a separate judge dependency failure;
20 has an actual official0. Do not present an eight-task failure rate from provider
errors. The runner exit code is0 but manifest is INCOMPLETE, not successful ten-task
completion. Original failures/partial trajectories remain untouched.

Observed application errors across the run: five CONTEXT_BUDGET_EXCEEDED,
two citation_validation ValueError, one composition_model ModelInvocationError.
These counts do not mean independent root causes. No runtime changed during run,
no task replaced or rerun, and no new repair is inferred from an error count.
Task19's eventual correct choice does not establish which internal planning
mechanism caused it; local target_trace does not expose enough prepare-call history
to attribute improvement to operation_plan. No claim of full R6/R7 closure.

Next: provider balance must be restored before the unfinished eight can be rerun
as separately labelled attempts. Preserve this run. Investigate context-envelope
growth and response failures against these captured inputs before adopting a
further production change; do not increase a token limit merely to erase failure.
