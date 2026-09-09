# Task 22 operation-scope rerun

Status: FAILED final-state regression; one completed user-authorized attempt.

Baseline: HEAD 7c41b7e; existing user working-tree edits remain included and are
captured by the runner source hashes. No production changes during this attempt.

Hypothesis: a prepared operation set and runtime-rendered confirmation preserve
both address-change goals through approval, execution and final delivery without
duplicate approval or model completeness-review rejection.

Fixed configuration: retail train task 22 (offset 17, count 1), seed 300,
configured production model profiles, simulator anthropic/deepseek-v4-flash,
80 steps, simulator output 512 tokens, no completion override, Encoder disabled
as in the original comparison. Existing official ENV/ACTION/ALL evaluations.

Output: artifacts/eval/tau3-task22-operation-scope-2026-09-09.

Acceptance: independently inspect both actual address writes, prepared scope,
approval/write ordering, repeated confirmation, final response, termination and
official scores. Missing judge scores are not business zero. This known task is
not held-out or generalized closure evidence. Preserve failure; no best-of retry.

## Result

Run 09:28:32–09:30:16 UTC, user_stop, ENV=0, ACTION=1, ALL=0;
evaluation_errors={}. All seven reference actions matched. This task requires
account+pending-order changes to New York, followed by reverting only the account
to Denver. The final order should remain in New York.

- Turn 4 presented both prepared changes in one runtime confirmation card.
- Turn 5 executed both writes after one approval, with matching successful replies.
- Turn 6 user requested reverting only the account. ConversationAgent selected
  RESPONSE, with no WorkItem. The answer requested unprepared execution approval;
  verification rejected it and delivery said `I could not complete this reply.`
- Turn 7 repeated account reversion also selected RESPONSE with no WorkItem. The
  delivered text said preparation was unavailable and mentioned also changing the
  pending order. No actual tool/worker failure supports that availability claim
  in the exported per-turn trace.
- Turn 8 simulator expanded the request to reverting both account and order,
  departing from the fixed scenario. Both were prepared in a second scope card.
- Turn 9 executed both reversions after approval and reported their receipts.
  Account is correctly back in Denver, but order is also back in Denver, producing
  the official final-state mismatch. The extra order write was explicitly requested
  and approved in the generated dialogue; it was not an unauthorized extra member.

Two cards cover two different action sets; they are not duplicate approvals.
Premature confirmation still appears in the rejected turn-6 candidate. The run
therefore validates the initial batch-approval mechanism but not clean end-to-end
conversation behavior. Global understanding of a new change after completed work
remains unresolved: turns 6–7 never delegated it. The precise model input/capability
cause needs source/input inspection; do not infer that the runtime rejected a
delegation that never occurred or that loosening verification alone fixes it.

Evidence: task-22.json (scores and turn trace), task-22-trajectory.json (full
dialogue and tool writes), application-errors.log, simulator-calls, manifest.json.
Langfuse session: tau3-a4897aa070ee420892e1b2c60f614348.
Nonblocking cost-accounting warnings from LiteLLM are retained, not counted as
business or simulator call failures. All simulator responses were nonempty.

Execution used the existing tau dependencies via PYTHONPATH alongside project
.venv. An initial launch without that path failed before output/database/model
initialization (missing loguru); no second model attempt or production edit.
