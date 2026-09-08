# Fixed-ten convergence regression — execution finished, convergence open

Source candidate: `955af21`. Same ten development tasks and budgets declared in
`plans/action-dialogue-convergence-2026-09-07.md`. Original process54813 exited0;
all ten trajectories/results exist. Manifest INCOMPLETE retains evaluator
unavailability; it does not mean that the execution process is still running.
These are development tasks, not a held-out score.

| Task | Official ALL | ENV | ACTION | Final internal completion | Inspection |
|---|---:|---:|---:|---|---|
| 4 | unavailable | 1 | 0 | true | Both pending-order changes committed; completed-first/pending-second reply retained correct scopes |
| 6 | 1 | 1 | 1 | true | Only lamp exchanged after user withdrew bottle; no post-write context failure in this run |
| 7 | 1 | 1 | 1 | true | Lamp exchange completed; clarification wording still contains a potentially redundant resolved-lamp confirmation |
| 8 | 0 | 0 | 0 | false | User repeatedly withdraws bottle exchange; system insists on both items, then ends DOMAIN_OUTCOME_REJECTED |
| 10 | 1 | 1 | 1 | true | Handoff performed; initial clarification is generic, intermediate BLOCKED must not be mistaken for final failure |
| 11 | 1 | 1 | 1 | true | Two returns committed; denial plus changed refund instructions loses the modification until another turn |
| 13 | 0 | 0 | 0 | true | Four-item return commits, but hidden target excludes keyboard; simulated user explicitly accepted the wrong set; compound approval reply also stalls |
| 14 | 1 | 1 | 1 | true | Both returns commit with correct operation scopes; initial generic question and redundant target selection remain |
| 15 | 1 | 1 | 1 | true | Boot variant/payment choice and modification complete; availability tradeoff is a genuine user choice |
| 16 | unavailable | 1 | 0 | true | Two cancellations plus watch return complete; initial wording requests bundled approval although only one operation is prepared |

Six official passes, two failures, two unavailable. Eight DB passes. Nine final
execution-completion flags are true, but task13 proves that this does not mean
the hidden business target was satisfied. Do not call this customer-service
closure: generic questions, redundant confirmations and compound-input losses
remain. Preserve the asyncio pending-store-task teardown warnings too; no
evidence yet that they lost a business write, but resource cleanup is not clean.

Task8 falsifies integrated closure: a once-per-order exchange restriction is
repeatedly presented as a requirement to exchange both original items despite
the user's explicit decision to skip the bottle. No write occurs and the user
eventually gives up. The trajectory establishes the symptom and ignored scope
correction, not yet whether the stale objective originated in planning, resume,
domain review or reply composition. Inspect linked producer/consumer inputs
after collecting the batch; do not add a bottle/lamp policy exception.

## Proven shared input/goal mechanism

Task8 actor observation35ef7f6704523d5a correctly proposes lamp-only exchange.
Reviewer8250810199cbf5cf rejects it because the assigned objective still requires
both items; 5770b9b55f479743 repeats that interpretation after another explicit
withdrawal. Finally099ec64099fea8bb and0ada49e846c27952 reject the user's decision
to skip the exchange altogether. This is not lack of tool capability.

Source chain: evaluation adapter attaches pending interaction identity to every
free-text response → DeterministicResolver emits REPLY_PENDING_INPUT based on
identity and text presence → Manager consumes the interaction before planning →
StateBoundTargetUnderstanding resumes the original WorkItem objective without
calling ConversationAgent. The reviewer is then asked to enforce a stale goal.
Changing its prompt to treat user text as an implicit goal revision would move
authority rather than repair goal ownership.

Independent review found related compound-input losses: task11 denies an
approval but discards the accompanying changed refund instructions; task13's
pending FIELDS takes precedence over APPROVAL, so choosing Visa and requesting
execution only completes the clarification side-task. It requires another turn
to approve the old action. The two pending objects may have different checkpoint
threads, while PreparedTurn currently selects one. Do not consume both signals
without actually resuming or explicitly resolving both affected tasks.

## Task13 benchmark attribution

Reference return IDs: 4579334072,6117189161,4947717507. Actual return also includes
1421289881 (Mechanical Keyboard). The original hidden user target excludes
keyboard and mouse. Assistant message24 misclassifies the keyboard, but simulated
user message25 explicitly accepts all four items. Actual write message44 and
success response46 agree with that visible confirmation. Record both agent
selection error and simulator goal inconsistency; do not call the final write
unapproved, alter the official reward, or feed hidden target data to the Agent.

Task13 also exhibits real response continuity failures independent of scoring:
after the selected refund method is supplied with assent, one reply falsely
claims processing, then revision asks again; verification ultimately falls back.
The following user turn finally approves and commits the original action.

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
