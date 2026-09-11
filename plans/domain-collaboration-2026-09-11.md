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

- done: traced dispatch, dependency projection, approval binding and observation.
- done: explicit reassignment handback using existing assignment_issue; WorkPlan
  selects same-control adjacent-revision investigation sources from the existing
  accepted_observed_outcomes checkpoint. Runtime passes unexpired facts with their
  original provenance; no old messages, grants or completion state are inherited.
  Multiple explicit handbacks retain these authorized inputs without requiring
  each intermediate domain to own their original tool authority.
- open: cross-domain combined-operation preflight and default return/exchange
  business capability. Requires real operation semantics and preparation lifecycle
  changes; a generic DAG check or a name-based prohibition is not an implementation.
- open: preparation failure lifecycle. Existing preparation maps dependency
  failure to a generic BLOCKED result; the native loop may continue and a later
  explanation may be treated as COMPLETE for an untyped open-ended goal. A local
  typed-failure projection trial was reviewed and withdrawn: it did not account
  for later handbacks or multiple preparation calls. This needs a coherent
  preparation-attempt outcome contract, not a precedence special case.

No additional router, execution engine, automatic compensation, or retry of
unknown writes. This item is not globally closed while the combination-preflight
gap remains. Existing unrelated worktree changes remain excluded from delivery.

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
