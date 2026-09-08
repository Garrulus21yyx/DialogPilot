# Target lifecycle v7 — fixed development regression

## Result

Both requested business mutations executed once, following one action confirmation each. Both business-completion replies agreed with the returned tool state and both corresponding application outcomes had `task_completed=true`, `verified=true`, with SUCCEEDED work outcomes. The complete conversational batch did **not** pass: task 3 subsequently failed on the user's courtesy closing message. No selective rerun or production change was made during this batch.

| Check | Retail train 2 | Retail train 3 |
|---|---|---|
| Requested business change | Return three delivered items | Modify a pending small T-shirt |
| Action confirmations before write | 1 | 1 |
| Business write calls | 1 | 1 |
| Tool result | `return requested` | `pending (item modified)` |
| Application completion after write | true; all recorded outcomes SUCCEEDED | true; all recorded outcomes SUCCEEDED |
| Successful user-facing business reply | Yes | Yes, before the final courtesy turn |
| Official ENV/DB check | 1.0; database match | Not obtained: simulation aborted before evaluator |
| Official ACTION | 0.0; one unmatched read | Not obtained |
| Official ALL | Unavailable: judge provider/credential error | Not reached |
| Conversation termination | `user_stop` | Application planning failure on turn 4 |
| Simulator empty outputs | 0 | 0 |

These are exposed train/development tasks, not held-out performance. Do not turn this result into a global success rate or claim the entire architecture is closed. Counts of business writes and confirmations were checked against the preserved trajectory, not inferred from the model's completion statement.

## Task 2 evidence

- One `return_delivered_order_items` call for order `#W2378156`, item IDs `4602305039`, `4202497723`, `9408160950`, original payment method `credit_card_9513926`.
- User explicitly approved after the action summary. No second confirmation request appeared between this approval and the write.
- The final reply reports the items as `return requested`, not a completed refund payment.
- The independent T-shirt count result remains SUCCEEDED alongside the committed return and completed domain objective.
- ACTION's unmatched reference is `get_product_details(product_id=6086499569)`, a read. The reference write matches. This does not override the successful DB check, nor is ACTION silently discarded.
- ALL fails in the official natural-language assertion judge invocation; the run has no OpenAI credential. `official_reward=null`, not zero.

Sources: [task outcome](task-2.json), [full trajectory](task-2-trajectory.json).

## Task 3 failure attribution

The successful change is independently visible in the tool return: order `#W4776164` contains replacement item `9647292434` with purple/polyester/S/v-neck options and price 53.48. The original item was 53.43; the environment records an additional payment of approximately 0.05. The application records committed receipt `official-call:tau3-call-d25bccecd21d40c0b91a4d93507e5b41`, completed workstream, and SUCCEEDED action/domain outcomes.

After that success, the user says: “No, that’s all I needed. Thanks for your help!” There is no STOP marker in this simulator response, so the official orchestrator correctly sends it as another application turn. It is not an empty simulator message.

Failure chain:

1. Conversation planning returns `{"status":"resolved"}` without goals or an approval decision.
2. `core/structured_model.py` rejects it against `planning_output_schema`: resolved requires at least one goal or an approval decision.
3. The application records `planning_invalid_provider_output`, stage `planning`, code `CONVERSATION_PROVIDER_OUTPUT_INVALID`, and a failure response ID. The earlier committed write and successful reply remain intact.
4. `evaluation/tau3_full_adapter.py` accepts NeedsInput or Completed; it raises on Failed rather than handing its already-published service notice to the simulated user. Simulation stops at step 27, before scoring.

The immediate failure is an invalid planning result on a non-action closing turn, **not** a stale approval, repeated write, BLOCKED-after-success result, empty simulator output, or missing judge credential. The supported global planning schema has no explicit response-only/no-new-work result. That is a relevant contract limitation to examine together with closing-turn handling; merely allowing arbitrary empty resolved plans would not establish a valid reply/publication contract. The adapter's handling of already-published failure notices is a separate evaluation-boundary limitation. This batch diagnoses these facts; it does not implement a fix or claim their root-cause repair is complete.

Sources: [task outcome and stage evidence](task-3.json), [partial trajectory](task-3-partial-trajectory.json), [simulator state](task-3-user-state.json), local `application-errors.log`.

## Execution and limits

- Manifest pins project HEAD `2b6b43f` (includes lifecycle changes `3473562`), benchmark commit `a2c024725189473d2d7cea3a5cfdbcc67478e41f`, seed 300, train IDs 2/3, 80 steps, 4096 completion override, 512 simulator tokens, Flash worker and Pro reviewer.
- Used the existing runner and production Target composition; local PostgreSQL database and Redis socket were isolated. The runner dropped only its randomly named evaluation database after saving artifacts; no real customer orders were changed.
- Project virtualenv supplied application dependencies; existing benchmark source and benchmark virtualenv site-packages supplied tau dependencies. No package installation or application code modification.
- Manifest records unrelated dirty working-tree state and source hashes. This is not a clean causal model ablation.
- Post-run hash comparison found a concurrent change to `infrastructure/target_conversation_provider.py` (current-turn prompt projection). This evaluator made no such edit and did not rerun. The composition imports that module at process startup; the manifest's startup hash, not the later working-tree file, identifies this run's provider source.
- Simulator diagnostics show complete capture and no empty outputs for both tasks. Existing Langfuse instrumentation remained enabled.
- No task rerun, new prompt, schema relaxation, or score replacement during validation.
- All original outcomes remain in this directory, including failed task 3. Full global/conversational closure remains unverified.
