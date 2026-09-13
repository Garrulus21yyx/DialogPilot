# Prepared action aggregation

Status: contract_verified — aggregation enabled on the single Target path.
Baseline: 33b0857, 2026-09-13; existing dirty runtime/evaluation work is user-owned.

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
2. done: candidate barrier, resource ordering, typed choice and explicit version limitations.
3. done: approval, compilation, cancellation, serialization, response and retry continuity.
4. done: generated algebra, SQL/native round trips, PG parallel recovery and fresh-context review; final isolated checks passed.
5. done: bounded contract and scoped delivery prepared; containing Git commit and final handoff record delivery identity.

## Current protocol

Shared root cause: a worker's prepared candidate was treated as user-facing
pending approval. The first worker occupied the slot and had to describe sibling
future actions. Derived dependency blockage was also persisted as a real worker
result, obscuring unstarted work on recovery. The migration repairs these owners
together, not only the collector's old multiple-proposer exception.

| Owner | Positive contract |
|---|---|
| Domain worker | Own permitted concrete actions and eligibility checks; returns PREPARED, not a grant |
| LangGraph runtime | Parallel read-only preparation; checkpoint actual worker results, not ResultBoard's derived BLOCKED |
| Aggregation | Current controls and current/retained candidates; registered resource/target groups define readiness |
| Missing preparation | Retain successful candidates and use the existing field/evidence wait; no silent subset approval |
| Failed preparation | Explicit choice: retry unfinished preparation or accept a prepared subset |
| Compatibility | Registry state search and dependency ordering; typed incompatible/unresolved outcomes, never model-invented effects |
| Selection | Existing PendingInteraction, exact alternatives and operation exclusions; selection grants no execution permission |
| Approval/execution | One exact PendingApproval, member origins/dependencies, existing compiler, governed writes and Receipt recovery |

Business-success edges are not preparation edges. If B needs A's completed
business result, approve only ready A and retain B's unstarted dependency closure
with A's continuation. PREPARED does not satisfy successful-task dependencies;
the reply must not say B is already prepared. Independent preparation is parallel.

An independent ready group is included in every alternative when another group
requires a choice, avoiding a second approval slot. Ordinary missing fields may
coexist and be answered partially. Choice IDs include accepted state and participant
fingerprints, so retry cannot reuse consumed signals. Operation exclusions survive
ordinary continuation and retire only on an explicit new goal revision.

### Bounded support and migration

- State feasibility is necessary, not live execution permission. Same-resource
  optimistic-version writes require owner-defined receipt/version binding, which
  the current registry does not provide. Return ACTION_VERSION_REBINDING_REQUIRED;
  never guess V+1 or approve both against stale V. Users may explicitly select an
  independently valid prepared subset. Unknown rules and search exhaustion are
  unresolved implementation limits, not invented business incompatibility.
- Search is bounded to 4096 states. Maximal alternative enumeration covers at most
  10 candidates; larger groups offer independently valid singleton choices rather
  than claiming exhaustive subset analysis. No extra model call chooses policy.
- Selection preserves exact candidates without redundant preparation. Existing
  expiry and live tool/version checks remain mandatory. Joint approval is neither
  an atomic transaction nor permission for automatic compensation.
- Deleted application/operation_plan.py and its old tests. Removed future-action
  DSL from tool schemas, middleware, domain-review instructions, prompts and the
  action-boundary probe. Only actual candidate aggregation remains usable.
- Mixed preparation batches expose no partial ready set; successful artifacts
  stay in native working state. Archive failure retains the same candidate envelope.
- SQL conversation state stores alternatives/exclusions. Native codec normalizes
  tuple fields; rebase and cancellation preserve per-origin authority. Production
  Run locking/checkpoint joining remain the sole execution coordination mechanism.
- Old WAITING_APPROVAL candidate checkpoints are not auto-replayed under PREPARED.
  Deployment must drain old waits on their pinned release or explicitly reconstruct
  candidates/origins before activating the new contract. No live records or receipts
  were deleted, migrated or re-executed here. No automatic legacy fallback remains.
- No new scheduler, grant store, queue, LLM reviewer or rollback system. The barrier
  cannot detect a user goal the planner never represented; semantic completeness
  is not proved by counting candidates.

### Current acceptance evidence, 2026-09-13

- 729 generated three-operation combinations compared with an independent exhaustive
  state simulator; target, dependency, unknown-rule and version-boundary cases.
- Choice tests cover mixed fields, cancellation, two failed retries then selection,
  explicit new-goal exclusion retirement, staged descendants and incomplete batches.
- PostgreSQL/native tests prove parallel overlap and restart resumes only the missing
  worker, retaining the successful candidate. Existing send/cancel/UNKNOWN and partial
  success checks remain. The expanded related suite: **698 passed, 3 skipped, 1 warning**.
  Skips are not proof. Additional cross-domain/select-and-write/Run/control/partial
  recovery suite: **280 passed, 2 skipped, 1 warning**. These suites overlap; their
  counts are not summed into a unique-test total or a business benchmark score.
- Independent fresh-context review identified choice identity and exclusion lifetime
  defects; both fixed at the state owner with regression tests. Final static review
  found no evidence-backed blocker; it did not run tests or attest benchmark quality.
- Failures were investigated rather than hidden: obsolete serialized-preparation
  fixture expectations were migrated; persisting derived BLOCKED was a real runtime
  authority defect and removed. No paid model/τ³ benchmark was run.

### Final isolated delivery validation

Exported only the staged index to a clean snapshot (no unrelated workspace
modifications), then ran the 20 related test modules with
`RUN_POSTGRES_TESTCONTAINER=1`: **737 passed, 3 skipped, 1 warning** in 77.21 seconds.
This includes the final planner/assembler choice-context and coverage changes.
The earlier isolated attempt exposed incomplete SimpleNamespace test fixtures and
unnecessary null context injection; fixtures now use the real PendingInteraction
contract, and ordinary replies receive no extra choice context.

Planner and composer now consume one structured, state-owned choice view. On a
reply-only recovery turn, alternatives remain available without tool execution;
internal JSON is not passed through as a ready-made customer question. Completion
and readiness consume ResultBoard's existing coverage contract, including required
receipts and conflicts, rather than treating any SUCCEEDED label as completion.
Final fresh-context static review confirmed these owner boundaries without finding
an evidence-backed blocker. It did not replace the automated checks.

Scoped delivery excludes existing async IO, RAG, tracing, evaluation-control-plane
and benchmark artifact edits. No production deployment, live-state conversion or
paid benchmark is part of this contract verification.

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

### Historical foundation follow-up (superseded above)

The preceding foundation-only evidence did not enable aggregation. The current
protocol above replaces that gate, without claiming atomic multi-write execution
or receipt/version binding absent from the registry.

No paid benchmark, production deployment or live checkpoint migration performed.
