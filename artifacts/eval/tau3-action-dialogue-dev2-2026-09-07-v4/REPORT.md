# Original-task regression at 50c3201 — NOT CLOSED

Fixed train tasks 2 and 3, seed 300, completion budget 4096, user budget 512.
Both simulations terminated normally. No substitution, best-of selection, or
changes to official scoring. All previous runs remain available.

| Task | ENV replay | Writes | Customer/runtime result |
|---|---:|---:|---|
| 2, count variants + return items | 0 | 0 | Return abandoned; final current-plan `task_completed=true` is not completion of the original request |
| 3, count variants + modify items | 1 | 1 | Eventually modified correctly; intermediate verifier failure and repeated confirmation remain |

ALL reward is unavailable because the default official judge lacks its OpenAI
credential. This is distinct from task 2's actual ENV=0. Auxiliary ACTION=0
is preserved, not overridden by local tests.

## Task 2 causal evidence

Langfuse session `tau3-941e57e4dd4d471b9de3e1326fd62888`:

- Planner generation `3c2bff2f62acda20`, trace
  `b86837e845baaefd93982be70ddb2136`, correctly produced two objectives:
  g1 counts variants with action proposals disabled; g2 processes the return
  with action proposals enabled. The planner did not omit the return permission.
- Both same-owner domain executions processed the complete original message.
  The count worker asked for return identity via the input tool. The return
  worker ended with a natural-language identity question rather than an input
  tool result. Empty required evidence plus nonempty text was interpreted as
  SUCCEEDED.
- Pending interaction consequently resumed the count objective. Generation
  `d30e295e523f37e8` confirms its retained objective was counting variants and
  it had no action proposal tools. No return write occurred.
- Final verification was bound to current output/facts, but its evidence lacks
  the per-result original objective. A valid-looking question could therefore
  be accepted under the wrong task; prose termination could masquerade as task
  completion. The resumed small plan no longer represents the entire request.

This is a shared goal/result ownership and completion-contract defect, not a
reason to grant write tools to the count task, parse customer confirmation by
keywords, or label BLOCKED as success.

## Task 3 remaining failures

One verifier rejected a supported price/availability answer while its own
explanation described those claims as supported. The public response fell
back to an unverifiable-detail notice. The later missing-input question mixed
execution permission with checking other orders, followed by formal approval.
The final write and reply were correct, but the interaction is not clean.

## Scope of the submitted repair

277 targeted tests (including PostgreSQL) pass for typed input rejection,
bounded domain repair, preserved independent results/facts, exact receipt
projection, and durable completed state even if later questions fail.
These do not prove semantic objective completion. The five-case component
replay also does not supersede the business regression failure above.

Status remains open. Before ten new tasks, review and close the contract from
accepted objective → scoped domain context → execution outcome → objective
assessment → pending/resumed work → public task-completion reporting.
