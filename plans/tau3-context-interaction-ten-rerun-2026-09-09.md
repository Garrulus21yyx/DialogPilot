# Fixed ten after context/interaction convergence

Status: INCOMPLETE — eight simulations finished; task29 interrupted and task30
blocked by provider HTTP402. User requested rerun after 9314d21.

Gap: ordinary interaction over-review, oversized pinned context and large current
tool results. Hypothesis: the simplified publication and bounded working context
reduce unnecessary model calls and budget failures without losing waiting bindings,
evidence or business completion. This is development regression, not held-out proof.

Data: official retail train offset14/count10 (19,20,21,22,23,24,25,28,29,30).
One attempt each, seed300, max_steps80, user_max_tokens512, no completion override.
Same configured models as prior run; Encoder disabled for baseline comparability.
Use existing runner, SDK, isolated database and diagnostics; no runtime edits during
run. Source hashes capture existing unrelated dirty changes. Metadata-only runner
correction records PREPARE_ACTION as the actual domain semantic review scope.

Output: artifacts/eval/tau3-context-interaction-ten-2026-09-09.
Budget: ten simulations and existing official ENV/ACTION/ALL evaluators only;
no selective reruns or extra model experiment. Preserve provider/evaluator errors,
partial trajectories and failed results. No alternative judge or provider switch.

Metrics: termination, actual business tool writes and receipts, user-visible reply,
repeated questions, context/format errors, official ENV/ACTION/ALL separately.
Task19 mutually exclusive writes require choice, not both operations. Task25 score
alone does not establish return completion. Missing judge score is not business0.
Adoption: inspect all tasks against requested goals and execution evidence; a
provider-blocked run cannot establish quality closure. Record limits and next step.

## Result (UTC 02:34:00–02:49:14)

Executed using the existing tau virtualenv with project site-packages and tau source
on PYTHONPATH. Runtime commit9314d21, tau a2c024725189473d2d7cea3a5cfdbcc67478e41f.
One attempt per selected task. No runtime code changed during execution. Original
output directory contains manifest/source hashes, per-task results, complete or
partial trajectories, simulator call captures and application errors.

| Task | Observed execution / user delivery | ENV / ACTION / ALL |
| --- | --- | --- |
|19|Identity/order/catalog reads and calculations, no return/exchange write. Reply incorrectly says same-order return and exchange can both happen. Later planning output incomplete; final savings reply does not complete requested business.|0 / 0 / unavailable|
|20|Initial order upgrade blocked by a five-read batch converted into a max-four-goal schema. User moves to another order; four-item modification on W9911714 actually writes, gift card charged75.30. Original scope incomplete; repeated confirmations, wrong equal item prices in success table and later citation failures.|0 / 0 / 0|
|21|Two item changes on W9911714 execute,41.92 gift-card difference, final balance44.08 reported. Repeated confirmation remains. ACTION misses two get_product_details and calculate reference calls, not the write.|1 / 0 / unavailable|
|22|Account/order address reads complete. Repeated confirmations; operation_plan next_step/tool mismatch rejected; neither address updated. User subsequently withdraws.|0 / 0 / 0|
|23|Two exchange tools and one pending-order modification execute across three orders; final reply summarizes all three.|1 / 1 / 1|
|24|No cancellation after user decides to retain grill; answers subsequent shirt questions. No write is expected by this retained-order result.|1 / 1 / unavailable|
|25|Tracking read; user insists on an ineligible Amex refund destination and requests human help. transfer_to_human_agents actually called; no return write claimed. This is a handoff outcome, not completed refund.|1 / 1 / 1|
|28|Three returns execute; user does not want entire pending order cancelled, so cancellation not executed. Final reply totals918.43. ACTION misses calculate, all reference return writes match.|1 / 0 / unavailable|
|29|Only skateboard exchange on W3792453 executes. Garden-hose preparation rejected by operation-plan/tool mismatch; a later false hose-success reply is rejected because receipt belongs to skateboard. Two subsequent replies say no result. Provider balance then interrupts simulation.|not evaluated|
|30|Initial simulator request fails HTTP402 before substantive user input; no business task evaluated.|not evaluated|

Four unavailable ALL scores (19/21/24/28) are official OpenAI judge missing
credentials, distinct from provider402 on29/30. Do not replace them with0. Existing
official evaluator returns ALL without that judge when its other gates determine
the outcome; the available ALL rows remain as produced, not imputed.

Eight completed simulations: five ENV1 and three ENV0. This is neither a ten-task
success rate nor proof of good user replies. ALL is available for only four rows.

## Failure attribution / limits

Application log records zero CONTEXT_BUDGET_EXCEEDED events in this attempt.
That observation supports absence of the previous symptom here, not global context
closure or causal quality improvement from an uncontrolled single run.

Three planning protocol failures: one stop_reason max_tokens/refusal rejection
(planning_output_incomplete; logs alone do not distinguish those stop reasons),
one five-goal maxItems4 rejection, one ambiguous candidate enum rejecting the raw
order ID. Three domain action rejections share next_step/tool mismatch. Four reply
assembly failures: two citation-ID validation, one incorrect receipt grounding,
one verifier unavailable after provider quota exhaustion. Counts are log events,
not independent causes or failed-task counts. SDK pending-task cleanup warnings and
LiteLLM cost-model lookup warnings also remain; cost estimates are not asserted.

Do not infer the repeated-confirmation root cause solely from final text. Inspect
planner handback versus actual pending approval, proposal identity and operation
plan conversion together before changing contracts. Ordinary-question model-call
elimination remains deterministic-test evidence; this run does not isolate every
question's call count. Preserve false claims and partial writes as counterexamples.

Next: use these saved traces for a coherent review of planner/tool-batch conversion,
action-plan next-step identity and author/approval handoff. No selective rerun, cap
increase, extra judge, or per-order fix was performed. Paid replay needs restored
provider availability; official ALL also needs its configured judge credentials.
