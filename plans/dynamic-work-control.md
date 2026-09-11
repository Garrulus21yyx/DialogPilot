# Dynamic task changes and conflict boundaries

Status: in_progress — bounded control repair implemented; full cross-run dynamic
insertion remains open. Do not attest the whole feature as closed.

Scope: accept user additions/corrections during execution; preserve unrelated
work; stop superseded work at model/tool/publication boundaries; serialize and
validate conflicting business writes without inventing a second scheduler.

Contract: persisted goal revision defines when a correction becomes effective.
Unsent stale actions cannot commit or publish; already submitted operations keep
their original receipt/recovery identity. Independent work survives a correction.
Business object conflicts are distinct from goal-version validity.

## Evidence and root causes

- Aggregate fingerprint checks treated an unrelated accepted goal as invalidating
  all in-flight progress. Planning acceptance now merges disjoint owner transitions;
  overlapping decisions still conflict. Progress is reprojected from execution
  facts against live state, rather than merging stale approval/input projections.
- The runtime checked only the current goal. A changed ancestor could leave a
  descendant eligible. WorkPlan supplies the complete prerequisite closure;
  retained outcomes resolve provenance through current and retained WorkItems.
  Missing provenance fails explicitly. Revised goals use new work identities.
- A tool-side revision check preceded the network write, leaving a check/send
  race. PostgreSQL OperationLedger now checks the accepted scope while committing
  EXECUTING under the same conversation row lock used by state CAS. This commits
  send authority, not proof of remote success. Already-authorized unknown effects
  retain the original operation key and receipt/reconciliation path.
- Generic pre/post guards must not erase committed writes or block reconciliation.
  They guard read/Agent work; the governed write owner guards fresh submissions.
  Publication retains its existing strict, transactional snapshot check.

## Supported contract in this change

1. Accepted corrections invalidate old work and transitive prerequisites at the
   next cooperative check. No forced remote rollback is promised.
2. Disjoint accepted goals do not overwrite each other's progress. Stale waits
   cannot be installed after the relevant goal changed.
3. Correction before send-authority commit prevents sending; correction after it
   preserves receipt recovery. No business tool is rerun to retry state CAS.
4. Retired read results are excluded from current evidence, while historical
   records and committed write receipts remain intact.

## Open architecture boundaries (blocking full feature closure)

Fresh-context review found these are not solved by the bounded repair:

- Target Run claims are per job, not per conversation/checkpoint. Two Runs can
  resume the same waiting graph without a single-writer owner.
- Two Runs can prepare actions before either installs the single pending approval.
  The second proposal needs a durable queue/continuation owner; simply appending
  its WorkItem loses the source checkpoint and prepared output.
- Observation planning still requires its committed aggregate snapshot; independent
  changes between phases are not yet fully reconciled.

The experimental automatic approval-thread join/queue was removed after review.
It is not an alternate supported path. Existing two-wait `source_thread_ids` is
not an arbitrary multi-run queue: its import and retirement contracts are narrower.

Next design decision: reuse durable Run ownership to make graph execution single
writer, while separating control acceptance from the long-running execution so
cancellation is not queued behind it. Alternatively retain independent Run threads
and explicitly own preparation/wait transfer. Do not hold a database transaction
across model calls or treat a checkpointer as a concurrent Run scheduler.

## Verification and delivery

- Generated permutations of independent acceptance; competing revisions;
  transitive dependency chains and retained cross-plan provenance.
- Controlled async interleavings: unrelated turn versus correction, including
  result-produced/before-progress-commit races.
- Real PostgreSQL cancellation/send ordering, approved operation-set execution,
  receipt replay, registered writes and failure recovery. InMemoryOperationLedger
  is not evidence of transactional control authority.
- Independent fresh-context review: no additional blocking defect found in the
  bounded repair; explicitly retained the full-feature blockers above.
- Expanded regression initially exposed test-double contract gaps: fault wrappers
  omitted the ToolManager `read_reuse` property, throwing after a successful
  reconciliation and provoking same-operation replay. Wrappers now delegate the
  real manager property; business retry policy was not relaxed.
- The existing six-scenario HTTP test remains outside verified closure: its
  scripted planner emits `execute_refund`, which both HEAD and the working tree's
  planning schema exclude. Direct schema validation reproduces this error before
  any write. Do not restore the obsolete planner action to make this fixture pass.
  Real process-recovery and domain-approval PostgreSQL tests are separate evidence.
- Final deterministic fault suite: **200 passed** (88.60 s).
- Expanded owner/approval/checkpoint/recovery suite: **475 passed, 2 skipped,
  1 failed** (198.57 s). Failure: process-kill-after-write test did not reach its
  crash boundary within the existing 20-second bound. Standalone rerun of both
  process cases: **2 passed** (10.55 s). Timing cause is not proven; keep the batch
  failure, do not describe the suite as entirely green or increase its timeout.
- Fresh bounded interleaving/provenance/approval subset: **37 passed**; final
  canonical rebase property and turn/pending-input subset: **88 passed, 1 skipped**.
  These overlapping subsets must not be added into an inflated total.
- XML evidence generated at `/tmp/dynamic-control-faults.xml`,
  `/tmp/dynamic-control-verified.xml`, `/tmp/dynamic-control-followup.xml`,
  `/tmp/dynamic-control-process-repeat.xml`, and
  `/tmp/dynamic-control-integration.xml` (the stale HTTP fixture failure).
- Delivery is the scoped Git commit containing this entry; unrelated tau
  attribution modifications are excluded. Remote delivery is verified separately.

Do not modify unrelated tau attribution working-tree changes. No paid benchmark
run is required for deterministic concurrency validation.
