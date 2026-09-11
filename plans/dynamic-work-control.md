# Dynamic task changes and conflict boundaries

Status: bounded implementation verified — cross-run execution ownership and
approval queue implemented; deployment not performed. A larger-batch process
startup timing instability remains recorded below. No paid benchmark run.

## Active continuation (HEAD 12dc3fd)

The user explicitly requested completion of the remaining dynamic insertion
surface. Do not narrow delivery back to the already completed goal guards.
Unrelated tau attribution changes remain untouched.

Chosen direction: one existing durable Run ledger, concurrent planning/control
acceptance, single conversation execution owner, native checkpoint continuation.
Ordinary goal changes commit before waiting for execution ownership. Approval and
input consumption (including compound signal decisions) commit under the execution
owner, not during speculative planning. The original semantic decision is saved;
binding it against the same still-valid signal uses no second model invocation.
Queued execution retains the accepted plan/checkpoint. Native task-level parallelism
and business write ownership remain unchanged.

Concurrency scope is explicit: one executing Run per conversation, independent
WorkItems parallel within its WorkPlan. New turns can plan and accept ordinary
corrections while an old Run is executing. They join a waiting graph at the next
execution boundary; they do not concurrently mutate that graph while a Worker
is still executing it. This is a deliberate single-writer design, not live graph
mutation or multiple simultaneous writers.

Required acceptance properties:
- a new correction is accepted while the previous execution is paused in a tool;
- no two Runs write the same conversation execution/checkpoint concurrently;
- an independent queued goal survives an approval wait and resumes with progress;
- lease loss fences stale execution, write submission and publication;
- waiting for the execution slot does not consume a failure retry budget;
- process restart resumes the accepted stage without redoing planning or writes.

Work steps: owner audit/design complete; implementation and migration written;
generated/concurrent PostgreSQL tests passed; fresh-context review complete
with reported issues repaired. Delivery is the scoped commit containing this
entry; remote hash is verified after push.
Reference checked 2026-09-11: LangGraph interrupts and LangSmith double-texting
docs distinguish native checkpoint recovery from service-level run scheduling.
https://docs.langchain.com/oss/python/langgraph/interrupts
https://docs.langchain.com/langsmith/double-texting

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

## Cross-run boundary repair

The earlier bounded repair left a shared ownership gap, now addressed together:

- The existing Run row carries PLANNING / READY / EXECUTING. A conversation row
  lock and unique partial index admit one executor. An expired executor still
  occupies that position until its own Run is recovered or finalized.
- TurnGraph checkpoints prepare -> accept_control -> acquire_execution before
  execution. ExecutionDeferred propagates framework control flow, releases the
  READY lease, and does not create a Failed result or consume a retry. Claim
  generations still increase; actual failures and expired executing/planning
  leases are recorded separately for the failure budget.
- Before executing, independent action-capable work joins the existing approval
  checkpoint. Its accepted WorkItems are queued in the existing suspended scope;
  native checkpoint retains unstarted items/outcomes. Approval id/version and
  operation members are unchanged. Read-only explanation may still use a separate
  input checkpoint. This uses the same existing approval/resume contract, not a
  second queue of prepared operations.
- A queued approval is rebound deterministically so it includes subsequently
  queued continuations, never additional approved operations. Semantic answers
  remain bound to their original interaction identity/version; retired signals
  fail as ConversationStateConflict rather than answering a different question.
- Observation planning uses live state for disjoint aggregate changes and rejects
  a superseded originating scope. It never restarts business tools to repair CAS.
- State CAS, every write send-authority transition (including same-operation
  replay), Publication and native PostgreSQL checkpoint IO fence the current Run
  generation. Already returned write receipts can still be recorded. SDK loop,
  serialization and checkpoint algorithms remain framework-owned.
- The API runs bounded Run consumers (TARGET_RUN_CONCURRENCY, default 4), separate
  from projection dispatch. One slow invocation no longer blocks all planning.

The unsafe earlier thread-join experiment is not restored as a parallel path.
Joining now occurs only after execution acquisition; accepted-goal validation is
mandatory at the queue owner. Existing two-wait source_thread_ids stays bounded.

## Migration / operations

Stop old Workers before migrating. Drain or explicitly reconcile any started,
unfinished old Runs; migration 0039 refuses them rather than declaring them never
executed. Do not delete old receipts or auto-convert internal checkpoints. After
the gate passes, run the normal PostgreSQL migration command, then start the new
Workers. Pending unstarted requests may remain. Published historical records stay
readable; unpublished v24 turn checkpoints require explicit reconciliation rather
than automatic execution under v25.

The deployment preflight is intentional: an online migration racing old Workers
is not supported. No SQL transaction or checkpoint connection lock spans an LLM
or business tool call. Run ownership is in the existing ledger, not Redis.

## Historical verification at 12dc3fd

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

## Current verification (2026-09-11, cross-run execution ownership)

The initial combined run produced **569 passed, 5 failed**. The failures are
retained here, not represented as a green baseline:
- migration rejection was classified as database unavailability; the migration
  owner now exposes `MigrationRejected`, preserving the database cause;
- two envelope tests attempted to install duplicate global work identities.
  They now prove owner rejection of that alias, then test two valid identities
  cannot exchange continuation envelopes;
- the process tests missed their startup/crash boundary in that batch. An
  isolated rerun reached the boundary and exposed their direct executor bypass
  of execution acquisition. Both crash and recovery sides now use the new
  execution contract; timeouts and exactly-one-write assertions are unchanged.

Final changed-test checks: **38 passed** (native approval queue permutations,
signal rebinding, deferred checkpoint resume, bounded consumers, parameter
acceptance); **3 passed** (real PostgreSQL migration rejection and process kill
before/after write). These overlap the combined regression and are not summed.
Fresh-context review covered execution lease loss, expired-owner recovery,
signal identity/version, operation replay and migration errors. Findings were
repaired and the final error boundary uses the review's generic rejection name.
No new scheduler/checkpoint algorithm or legacy fallback was introduced.

Final causal-surface regression: **524 passed in 187.55 seconds**, covering the
15-file dynamic-control, domain approval, native queue, TurnRuntime, Run worker,
orchestration, state transition, WorkPlan policy, controlled business fault,
publication snapshot, approval revision/observation, pending input, parameter
acceptance and real process recovery suite. Process tests now report startup,
pool, business preparation, execution acquisition and checkpoint stages under
the same original total 20-second bound.

The larger 20-file batch remains **572 passed, 2 failed in 335.55 seconds**:
both failures were process startup/crash-boundary timeouts, not failed write
assertions. Fresh-context sampling during the successful 524-test batch found
the child processes CPU-running, initially without DB connections, then idle
COMMIT connections without blocking PIDs. This is evidence against a DB-lock
explanation in that run, not proof of the exact older timeout cause. Do not
claim the larger batch or whole repository is green. Separate PostgreSQL Run
ownership/checkpoint tests passed **15/15**; the migration/process final subset
passed **3/3**. Overlapping counts are not added.

Operational boundary: code delivery is not production deployment. Stop old
Workers and satisfy migration 0039's documented drain/reconciliation gate before
starting the new runtime. No production database, old receipt or unrelated tau
artifact was changed by this work.
