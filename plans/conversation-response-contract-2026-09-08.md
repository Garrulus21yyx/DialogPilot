# Conversation-only response contract

Status: in_progress. Baseline a34102c; preserve concurrent RAG/provider edits.

## Cause and scope
The planning algebra equates understood input with executable goals/approval.
Response assembly also requires an execution board or pending interaction.
Thus a valid conversational turn cannot reach normal publication. The benchmark
adapter separately discards published non-Completed outcomes.

## Positive contract
- `respond` carries one nonblank customer-facing candidate, no goals, missing
  fields or approval. Existing `resolved` still requires goals/approval.
- Planning owns this choice. Policy/compiler carry it as a RESPONSE plan with
  no work, transitions or cancellation. Existing waits remain unchanged.
- Existing response verification checks the candidate against conversation
  context; no mandatory second composition. Existing bounded revision applies
  only if verification rejects it. No business execution is authorized by prose.
- Publication persists/replays the same verified response. Reply-only does not
  attest business completion or consume a pending signal.
- Benchmark delivery consumes committed public replies without disguising
  typed application failure as success.

## Steps
1. done: trace and migrate schema, proposal, policy, plan, assembly,
   publication and checkpoint identity.
2. done: invariant/schema tests, waiting-state and publication integration.
3. in_progress: independent review passed; clean committed-snapshot validation
   and push remain.

## Review-driven contract completion
Raw text was also blindly coerced into a sole non-DELEGATED pending field.
Now prose is interpreted semantically. `resolved.input_values` proposes current
field values; Manager reuses the existing resolver and state-bound continuations.
Typed client fields retain their deterministic fast path. Independent goals and
approval decisions can coexist. Duplicate/revised target commands are rejected.
Unsupported WORKFLOW/ACTION field continuation uses typed TurnPlanningError.

TurnRuntime is v8; prior unpublished v7 checkpoint replay is explicitly rejected.
Plan text participates in identity; tuple normalization keeps checkpoint round
trips equal. No migration of old receipts or automatic re-execution is added.

## Evidence before clean snapshot validation
- Expanded working-tree tests: 289 passed, 3 fresh-database tests skipped with
  testcontainer configuration (separate fixture requires TEST_DATABASE_URL).
- New matrix plus state-resolution tests: 72 passed, 1 PG test skipped in that
  invocation; PG response restart/publication already passed in expanded run.
- Benchmark adapter unit tests with existing tau dependencies: 10 passed.
- These suites overlap; do not add their counts.
- Independent fresh-context review found no concrete blocker within declared
  scope after verifying input/approval/independent-goal combinations.
- Concurrent SDK interface migration temporarily caused two response tests to
  fail; current working-tree rerun passed after that external migration advanced.
  Clean snapshot verification must exclude it and other unrelated edits.

## Clean-snapshot checks
- Implementation commit: 83a7c3c. Isolated worktree at that exact commit:
  311 tests passed, zero skips, including PostgreSQL publication and restart.
- Additional HTTP/approval/Run/benchmark set: 41 passed, one HTTP fixture failed.
  Reproduced the same HTTP failure on pre-change a34102c: its scripted planner
  ignored typed approvals and emitted a second business goal, leaving the
  unknown-outcome scenario WAITING_APPROVAL rather than RECONCILING.
- Migrated that test double to emit the current approval decision contract.
  Decline assertion now checks its scripted not-submitted reply AND absence of
  the denied order's write, retaining final CANCELLED-state assertions. No
  production approval behavior was changed for this fixture.
- Updated HTTP test passed. Final combined snapshot run remains before push.

No keyword classification, new loop, engine fallback, Encoder change or real
model benchmark in this work item. Prior two tasks remain recorded failures.
