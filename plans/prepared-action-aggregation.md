# Prepared action aggregation

Status: in_progress — authorization foundation implemented; aggregation not enabled.
Baseline: 1a4059b, 2026-09-13; existing dirty runtime/evaluation work is user-owned.

## Positive contract

Domain workers prepare their own registered operations without creating approval.
The WorkPlan defines the preparation boundary; related resource/dependency scopes
must be ready before approval. ResultBoard projects outcomes, not business rules.
Registry-owned compatibility selects a bounded legal ordering or returns a typed
conflict/unresolved result. Approval binds exact operations and every originating
goal revision. Execution uses existing governed writes and records partial effects;
joint approval is not an atomic transaction or automatic compensation.

Do not re-query valid evidence or require another confirmation solely because
control crossed an Agent boundary. No second scheduler, grant store or fallback.

## Audit questions / invariants

- Trace preparation -> worker result -> graph readiness -> approval -> typed/user
  decision -> compiled writes -> receipt -> continuation and cancellation.
- Prepared candidates are not WAITING_APPROVAL until the approval owner binds them.
- Candidate collection must survive missing-input/failure/restart without silently
  approving a subset of a coupled request or losing candidate origins.
- Cancellation/revision of any participating origin invalidates its unsubmitted
  operations; other goals and committed receipts remain distinct.
- Compatibility uses registered effects, not model-invented state transitions;
  unknown rules and search exhaustion are not evidence of incompatibility.
- Existing user edits are preserved. No paid model benchmarks during migration.

## Steps

1. done: map owners, persistence and all consumers, including dirty changes.
2. in_progress: origin/authorization ordering contract fixed; candidate grouping and receipt-bound versions remain open.
3. in_progress: approval origins, compilation, cancellation, serialization and response authority migrated; preparation barrier not enabled.
4. in_progress: foundation properties and PostgreSQL send-boundary tests passed; fresh-context foundation review passed. Whole-protocol acceptance pending.
5. pending: document results and scoped delivery; preserve failures.

## Established causal surface

- `TargetActionPreparation` and framework result conversion currently call a
  candidate WAITING_APPROVAL before an approval exists; the graph therefore
  serializes investigation based on an approval slot.
- `bind_action_approval` rejects multiple proposing workers. ApprovalOperation
  has no per-member origin or dependencies; Pending/Accepted project the first
  member as owner of the entire scope.
- `StateBoundTargetUnderstanding` emits independent writes, then makes every
  origin continuation depend on all writes. This loses both ordering and partial
  failure independence.
- Approval consumption, revision, RoutePolicy and Publication use one origin.
  Updating only the collector would authorize other members under that origin.
- Ordinary continuation admission advances the origin revision before writes.
  Checking the old preparation revision forever would reject valid execution;
  compiler-owned approval-to-execution lineage must distinguish this advance from
  a later user correction.
- State feasibility is not version feasibility. Two prepared writes on resource
  version V cannot both assume V after the first commits. A receipt-bound version
  rule must be provided by the business owner; never infer it by incrementing or
  silently editing approved parameters.

Independent read-only review confirmed these boundaries, including per-origin
continuations, cancellation, PostgreSQL serialization and publication freshness.
The dirty async state/ledger IO changes are coherent with this design and must
remain untouched except for explicitly scoped contract edits.

## Implemented foundation (not full protocol closure)

- Each ApprovalOperation records its originating task/control and approved
  predecessor operation keys; every attributed member is checked against its
  suspended task envelope.
- Approval invalidation, input/approval isolation, conversation context and
  Publication consume all member origins, not just the first one.
- Approved continuations depend on their own writes, not every unrelated write.
  Compiler restores approved operation dependencies even if a command omits them.
- Compiler binds unsubmitted writes to the normally admitted continuation
  revision. WorkControlGuard and the PostgreSQL send-authority transaction read
  the same `authorization_controls`. Cancellation does not erase committed or
  UNKNOWN effects; existing receipt reconciliation remains authoritative.
- Assembler no longer turns a raw worker candidate into a pending approval.
  Only an actual PendingApproval supplies approval claims and the scope key.
- Native checkpoint and SQL payload loaders require explicit authorization
  lineage. Old persisted WorkItems require an explicit migration/reconstruction;
  they are not silently restored with empty authorization. No live data has been
  migrated, deleted or re-executed in this task.

### Evidence, 2026-09-13

`tests/test_approval_origin_lineage.py` covers per-origin cancel/revision,
independent continuation dependencies, compiler-owned order, SQL/native codec
agreement, candidate/presentation separation and real PostgreSQL send-versus-cancel
ordering (including UNKNOWN reconciliation).

Expanded approval/planning/control/runtime suite with
`RUN_POSTGRES_TESTCONTAINER=1`: **498 passed, 1 warning**. Earlier focused run:
**88 passed, 1 warning**. These are contract/integration tests, not a paid-model
or end-to-end customer-service score.

Additional PostgreSQL manager/write-workflow/publication/snapshot suite:
**156 passed, 2 skipped, 1 warning**. Skips are not counted as validation. No paid
model was called. The last collector cleanup retained the existing single-proposer
gate; it does not enable a second aggregation path.

An additional checkpoint-takeover suite exposed an outstanding fixture mismatch:
`test_takeover_preserves_colliding_local_ids_and_replays_without_source[memory-True]`
expects a read-worker RuntimeError to escape, while the current runtime converts
it to a typed worker failure. Neither runtime nor that fixture was changed here;
this result is retained and not counted as a passing crash-recovery proof.

Independent fresh-context review checked the foundation twice, including all
origin consumers and both submission boundaries. It does not attest to the
unimplemented aggregation protocol.

### Reference boundary

Checked LangChain's official Human-in-the-loop documentation on 2026-09-13:
https://docs.langchain.com/oss/python/langchain/human-in-the-loop
It provides persisted interrupts and decisions on exact proposed calls, including
multiple decisions in action order. Cross-domain preparation completeness,
business state compatibility and atomicity are application contracts, not a
guarantee supplied by HITL. No new scheduler or approval model was introduced.

### Remaining work / next action

Keep multi-worker aggregation disabled until the preparation-group contract is
closed. Next implement together: PREPARED versus WAITING_APPROVAL, related-group
readiness and retained candidates, typed conflict choice and goal revision, and
owner-defined receipt/version binding for ordered same-resource writes. Do not
remove the multi-proposer guard in isolation. A state-feasible ordering alone must
not be presented as an executable or atomic approval group.

No paid benchmark, production deployment or live checkpoint migration performed.
