# Domain collaboration: implementation audit

## Supported contract

WorkPlan owns task dependencies; ResultBoard projects readiness and preserves
independent results; LangGraph dispatches workers. Domain evidence crosses a
declared dependency through AgentContextView.dependency_results and facts.
User input and approval resume persisted waits. Unknown writes stay with receipt
reconciliation. ConversationAgent revises task assignments through the existing
bounded observation loop, never by directly executing a failed write again.

## Evidence-backed gaps

1. Ordinary report_blocked returned only BLOCKED. The observation loop accepts
   explicit assignment_issue, retryable reads and no-progress failures. Workers
   had no explicit way to request assignment repair without a semantic reviewer
   rejecting their preparation. Connect that existing contract to the interaction
   tool, preserving ordinary terminal blockage and missing-input semantics.
2. Cross-domain preparation is serialized, not coordinated as a transaction.
   bind_action_approval rejects more than one proposing worker. operation_plan
   validates DAG structure only; preconditions/effects are model descriptions.
   There is no authoritative multi-operation feasibility service. Do not claim
   one exists or infer incompatibility from operation names or owner-prefixed
   aggregate_ref. Default registry has no exchange/return write actions.

## Scope and progress

### Active follow-up: operation compatibility before preparation

Implement registry-owned resource state transitions, checked against the whole
declared candidate batch and its remaining operation DAG before any preparation
tool runs. Resource identity is independent of domain owner. This is a necessary
compatibility check, not current eligibility, a transaction, or proof that the
model listed every user goal. Existing semantic coverage review remains responsible
for omissions and unstructured policy. Submission still validates live business
state. No new router, additional model call, or action-pair exception table.

Validation must distinguish impossible combinations, feasible ordered sequences,
unordered noncommuting batches, different entities, missing declarations, and
user-choice recovery with no prepared approval from a rejected batch.

- done: traced dispatch, dependency projection, approval binding and observation.
- done: explicit reassignment handback using existing assignment_issue; WorkPlan
  selects same-control adjacent-revision investigation sources from the existing
  accepted_observed_outcomes checkpoint. Runtime passes unexpired facts with their
  original provenance; no old messages, grants or completion state are inherited.
  Multiple explicit handbacks retain these authorized inputs without requiring
  each intermediate domain to own their original tool authority.
- implemented and contract-validated: necessary compatibility of the complete declared
  operation group before preparation. ActionDefinition owns resource transitions;
  the SDK boundary checks the ready batch and remaining graph before tools/review.
  Preparation validates its declared graph; approval binding rechecks concrete sets
  from custom executors. Known conflicts use the existing user-choice handback.
  Default order cancellation/address/refund state prerequisites and official retail
  order transitions are registered from their business implementations.
- bounded limits: this is not a transaction or proof of live eligibility, payment,
  current versions, full policy, or semantic completeness. Unmodelled effects remain
  explicitly UNMODELLED_EFFECTS for the existing semantic review, not no-op effects.
  Sibling operations must be declared by the planner/worker for deterministic
  comparison. Undeclared objectives remain a semantic coverage concern; no global
  combination guarantee is claimed. Default return/exchange writers remain absent.
- open: preparation failure lifecycle. Existing preparation maps dependency
  failure to a generic BLOCKED result; the native loop may continue and a later
  explanation may be treated as COMPLETE for an untyped open-ended goal. A local
  typed-failure projection trial was reviewed and withdrawn: it did not account
  for later handbacks or multiple preparation calls. This needs a coherent
  preparation-attempt outcome contract, not a precedence special case.

No additional router, execution engine, automatic compensation, or retry of
unknown writes. Existing unrelated worktree changes remain excluded from delivery.
Registry fingerprints include transition rules, so an approval prepared against
different rules must not be silently reused. The grant/receipt lifecycle is unchanged.

### Compatibility acceptance evidence

Generated rule algebra compares 729 three-operation sequences and 729 unordered
two-operation batches with a future action to an independent exhaustive simulator.
Checks cover same/different resource IDs, valid sequencing vs mutual exclusion,
unknown effects, cross-resource dependency contradictions, owner-independent rules,
native cross-owner references without expanded permissions, zero preparation on
conflict, persisted choice, subsequent selected-action preparation, and custom
executor approval validation.

Fresh review caught unknown-as-preserve and existential unordered-batch acceptance;
both repaired at the checker and covered by the broader algebra oracle, not a
business-name branch. Ready batches require all allowed orders to preserve the
remaining goal. Search is bounded to 4096 states; budget exhaustion is typed
ACTION_COMPATIBILITY_UNRESOLVED, not a business incompatibility assertion.
Each remaining graph is local to its proposal; future operations are declared once
per batch, not deduplicated by guessing whether similar operations mean the same job.

Regression validation passed 503 tests using PostgreSQL after the unordered-batch
and approval-boundary changes (one existing multiprocessing fork warning). Final
focused validation passed 72 tests, covering three-operation restore-vs-choice diagnostics and
removal of the obsolete preparation_names argument. Independent review passed
18 tests and its separate unordered-batch counterexample. No paid tau3 simulation
or new business-effect score is claimed. This closes only the stated resource-
state compatibility contract, not semantic omission detection or all business policy.
The 72 focused tests also passed from an isolated checkout of the staged files,
without the workspace's unrelated tracing/context modifications.

## Independent review and validation

Fresh-context reviewer found that simply preserving facts in a returned result
did not make them available to a newly assigned owner. Repaired the plan/runtime
input projection at its owner rather than copying conversation history. A second
review checked multi-hop forwarding; the native two-FrameworkAgent handback test
now exercises planner revisions and a third owner consuming the original product
fact, with one catalog read and one independent order read.

Latest independent scope review found no additional concrete handback defect;
reviewer ran 14 tests, with 3 PostgreSQL cases skipped in its environment. Root
validation also uses the real PostgreSQL fixture. This does not validate cross-
domain combined write feasibility, default exchange support or real-model gains.

Root validation on the current working tree: 231 passed, one existing fork warning,
covering domain handback, assignment repair, framework agent, instructions, turn
runtime, orchestration runtime and action decision continuity. No paid model or
tau3 business run was performed. Independent unrelated changes were excluded from
the staged implementation, including existing framework tracing edits.
