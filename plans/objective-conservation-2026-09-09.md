# Accepted objective conservation

Base f4bd1db. No new agent/reviewer or goal database. Existing WorkControl binds
accepted objectives; WorkPlan retains unreplaced outcomes; pending interactions
own unanswered task fields. Do not infer completion from omission in a new plan.

Observed gap: ConversationState._input_revision_update discards the entire
multi-task wait when any suspended control changes. Independent unanswered goals
then lose their resumable envelope despite still having an active control.
Fix at the state owner: retire the changed dependency branch, retain unrelated
fields and suspended work, increment interaction version, consume only the prior
signal version. Original checkpoints remain associated with the retained wait.

Verification scope: permutations of independent goal revisions/cancellations,
dependency impact, persistence roundtrip, partial-input resumption, WorkPlan
retained outcomes and response delivery. Initial natural-language extraction and
semantic completeness are not established by these structural tests; no paid
model task is being run and no new mandatory online judge is added.

## Causal surface and implemented contract

The state loss had a compensating consumer: understanding automatically resumed
all untouched siblings whenever one pending task changed. That reran unanswered
work instead of preserving its wait. Both ends change together:

- ConversationState partitions the affected dependency branch, retaining
  independent questions and their original checkpoint identity.
- StateBoundTargetUnderstanding resumes addressed work and queued descendants,
  not independent tasks still waiting for answers.
- ConversationManager resumes the original execution checkpoint whenever the
  interaction changes, including partial retirement and cancel-only plans. Its
  execution-closure projection uses the same dependency partition.
- WorkPlan retains unreplaced outcomes; ResultBoard projects them. No extra goal
  store, completion authority, or reviewer is added.
- TurnRuntime v24 rejects unpublished older lifecycle checkpoints instead of
  interpreting their compiled auto-resume plans under new semantics.

The invariant is that modifying one accepted goal preserves unrelated goals,
unanswered fields, valid results and their continuation. A newer subset plan
cannot make an omitted unfinished task complete. Cancelling upstream execution
invalidates dependent execution, not unrelated user goals.

## Verification and limits

Tests enumerate every revision/cancellation order of three independent goals,
reconstruct state through PostgreSQL serialization, exercise dependency impact,
and cover retained outcomes from absent through successful and failed results.
Manager integration uses memory and real PostgreSQL state/checkpoints: two waiting
tasks, modify/cancel one, answer the other, and verify only that other task
executes on the final turn while both outcomes remain present.

The partial-delivery test fixture now explicitly makes recovery planning
unavailable; replaying its constant initial proposal was no longer a valid model
of the recovery-enabled runtime introduced in f4bd1db.

Initial semantic extraction, free-text response coverage, and all historical tau3
failures are outside this structural proof. No paid model run was performed;
this is not a declaration of global convergence or benchmark closure.

1. complete: scoped interaction transition and affected consumer migration.
2. complete: 440 passed with TEST_DATABASE_URL configured, covering objective
   conservation, partial inputs, approvals, task lifecycle, observation,
   PostgreSQL persistence, TurnRuntime, WorkControl, worker recovery and WorkPlan
   execution contracts. git diff --check passed. Counts are not semantic scores.
3. delivery: scoped commit/push to feat/customer-service-target-architecture;
   unrelated workspace changes excluded. Commit identity is recorded in Git.
