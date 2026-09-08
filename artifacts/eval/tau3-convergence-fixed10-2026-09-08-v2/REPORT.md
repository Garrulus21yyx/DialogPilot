# Fixed-ten convergence regression — in progress

Source candidate: `955af21`. Same ten development tasks and budgets declared in
`plans/action-dialogue-convergence-2026-09-07.md`. The original run remains live;
this is an incremental inspection, not a completed batch or held-out score.

| Task | Official ALL | ENV | ACTION | Final internal completion | Inspection |
|---|---:|---:|---:|---|---|
| 4 | unavailable | 1 | 0 | true | Both pending-order changes committed; completed-first/pending-second reply retained correct scopes |
| 6 | 1 | 1 | 1 | true | Only lamp exchanged after user withdrew bottle; no post-write context failure in this run |
| 7 | 1 | 1 | 1 | true | Lamp exchange completed; clarification wording still contains a potentially redundant resolved-lamp confirmation |

Remaining tasks not yet inspected. Do not interpret this table as three complete
quality passes.

## Task4 evidence

`task-4-trajectory.json` contains two `modify_pending_order_items` executions,
each followed by a successful result. Approval requests bind different orders
and newly disclosed price differences ($0.21 and $0.05); there is no repeated
approval of the same prepared operation. The earlier user's general assent to
the second update preceded disclosure of its specific difference. This is not
evidence that all possible mixed assent/selection cases are solved.

The initial catalog result contains 12 variants, 10 available, supporting the
reply's available-option count. Final task trace has only SUCCEEDED outcomes and
task_completed=true. ALL is unavailable because the official NL judge lacks
OpenAI credentials, not because of a business score of zero. ACTION=0 is the
preserved reference-trajectory mismatch for an additional product lookup.

## Task6–7 evidence and limits

Task6 no longer exhibits the previous successful write followed by an internal
context-budget failure. Its user's correction occurred before proposal approval;
the bottle remains unchanged and the final lamp result is successful.

Task7's new trajectory also completes, but it is not an exact replay of the old
post-proposal revision trigger. Before the user chooses a bottle, the assistant
identifies the lamp meeting the stated first preference and then asks “Would you
like that one?” alongside the genuine bottle choice. This may still violate the
resolved-target versus missing-choice boundary. Keep the semantic closure open;
inspect the producing request, accepted domain outcome and final composition
after the frozen batch, rather than declaring the score sufficient or adding a
lamp-specific rule. Selecting a payment method and subsequently approving a
complete proposal are distinct decisions; do not simply remove every second yes.

## Remote trace inspection

Read through the official Langfuse observations CLI on 2026-09-08, against the
[current tracing criteria](https://langfuse.com/docs/observability/best-practices).
Task4 session `tau3-044065fea74e44bbba683c8025f4be38` returned 459 observations
with no missing referenced parents in that snapshot.

Trace `d4a7758ca6b977a794a763783b437fc7` confirms domain reviewer generations
`732ba3384eaec06e` and `73392f522aff225f` use deepseek-v4-pro under
assess_domain_outcome → InteractionBoundaryMiddleware → retail_agent →
execute_work_item → customer_service_turn. Actor generations use Flash in the
same graph. This validates the specific role and parent-manager repair remotely.
Token usage is present; costs are null, not zero.

One related tracing gap remains: compose_response still starts a separate trace
(e.g. observation415138a560b8647c in trace5b196c23ca0dcd8e8daa11bd7b367f40).
Source inspection finds its plain-text model call still supplies a handler list
directly, unlike the repaired structured-call boundary. The session permits
correlation, but this is not full per-turn hierarchy closure. No production
change during this batch; include the direct model-call consumer in the next
coherent callback propagation repair.
