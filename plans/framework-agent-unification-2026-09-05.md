# Target framework Agent unification

Status: in_progress

Target delegated objectives use one create_agent runtime. Conversation planning, direct
tools and governed workflows retain their respective owners. LangGraph owns working
messages and step recovery; ToolManager retains execution evidence and permissions.

1. implemented: shared model/tool control middleware and actual call limits.
2. implemented: all six delegated domains use create_agent; deleted TargetAgentExecutor.
3. verified: PostgreSQL child checkpoint recovery after real process exit; completed tool
   called once, typed evidence survives recovery. ToolMessage requires a versioned artifact
   envelope because its serialization turns nested dataclasses into dictionaries.
4. in_progress: removed old /agent-runs read/resume and unused compatibility chat
   composition; API no longer constructs SQLite RunStore. Public ChatCommand/outcomes
   moved to chat_contracts, with all import consumers migrated. Old orchestrator/ReAct
   and compatibility evaluation consumers still need removal before deleting RunStore.
   Stateful loop-budget fixture now uses TargetFrameworkAgent; tool calls receive their
   framework call ID and trusted context through ToolRuntime, not user-bound closures.
   Removed application package's eager legacy-runtime export.
5. in_progress: remaining legacy runtime deletion and integration verification.
   BadCaseRegistry migration 0034 now uses the shared PostgreSQL pool; API and export
   CLI consume the same owner. Schema creation moved out of service startup.
   Per-fingerprint transaction locks and row locks preserve observation deduplication,
   lifecycle transitions and immutable review decisions across independent instances.
   13 owner/concurrency/API tests pass in the shared tree. Clean committed snapshot:
   26 pass across badcase closure, handoff helpers, startup and PostgreSQL migration.
   Three old test_chat_handoff cases still inject only AgentOrchestrator, then call
   the Target-only /chat endpoint: Target runtime is not ready. These are remaining
   cutover test consumers, not a reason to restore the removed compatibility facade.
   Full repository acceptance remains open until these consumers move to Target.
   CustomerOperations migration 0033 implemented and verified with 43 PostgreSQL tests:
   tenant-scoped keys/queries, exact-operation replay, independent-owner version ordering,
   competing cancellation/address actions, tool identity and existing read-only E2E.
   Removed SQLite business implementation, constructor consumers and path configuration.
   Native transaction locks replace process-local locks at order/account and operation keys.
   Evolution registry migration 0032 verified: only PostgreSQL implementation remains.
   Clean committed worktree: 36 passing tests including API startup, concurrent immutable
   registration, framework execution and PostgreSQL process-exit recovery.

Target path has no old-engine fallback. Repository-wide SQLite deletion is not complete.

Current cutover work: health and PerformanceMonitor now read OrchestrationRuntime
worker outcome counts and latency. These are process-local invocation observations,
not durable goal counts or verified answer quality. Waiting/cancelled/superseded
outcomes remain separate from completed-outcome success-rate samples. Monitoring no
longer changes routing weights. CLI is now an authenticated client of the existing
/chat and /invocations APIs; it submits once and polling never starts a second run.
Transport evaluation imports ChatHandler from shared contracts, not old ChatApplication.
39 focused runtime/monitor/CLI/evaluation tests pass. Clean committed snapshot startup
and the same focused suite: 40 passed, including PostgreSQL-backed API lifespan.

In progress: routing evaluation now uses TargetPlanningRunner, the same registered
understanding instance and TargetConversationManager.prepare with isolated in-memory
case state. It consumes real TurnPlan owner/work IDs, not old PlanningDecision.
Old intent labels are annotations, not another routing authority. API startup no longer
constructs AgentOrchestrator or injects Skill/ToolManager into an unused worker pool.
Full-execution evaluation awaits the original durable invocation's terminal outcome;
it does not score Accepted as a completed answer or submit a second message. Execution
scoring consumes Target work_item_ids and typed outcomes; empty expected tasks cannot
override failed coverage. The evaluator uses the configured runtime tenant.
Clean committed snapshot: 44 tests pass, covering API lifespan, actual Target published
result scoring, planner-only isolation, all legacy intent labels' routing non-authority,
durable completion waits/timeouts, PostgreSQL terminal reads, API datasets and CLI.
Then migrate legacy evaluation harnesses and behavioral gates before removing old
ChatApplication, ReActExecutionEngine and its RunStore.
Compatibility background execution is removed: application/compatibility_chat.py,
application/compatibility_execution.py and infrastructure/postgres_compatibility_execution.py.
Its dedicated tests were retired after moving repeated binding, concurrent claims,
same-worker stale epoch fencing, deletion fencing and terminal replay checks to the
PostgreSQL Target Run suite. 19 Target Run/planning tests pass on real PostgreSQL.
The existing execution table's historical name remains in immutable migration history
and the sole Target store; no business data or database files were deleted.
ToolManager's optional execution_store claim/replay branch and its private codec are
removed. Target writes use GovernedWriteRuntime and PostgresOperationLedger; the
manager still owns tool validation, execution, typed effects and cancellation audit.
The old SQLite resume tests for tool replay, crashed execution and expired claims
are replaced by the write-workflow contract suite on both ledger implementations.
Read-tool recovery belongs to the existing framework PostgreSQL subprocess restart
test, not a second tool-call lease. Legacy approval/checkpoint tests remain pending
the old ReAct/RunStore contract removal.
This migration exposed an owner-level persistence bug: operation reads were scoped,
but Target event IDs only hashed a logical operation key. The event writer now binds
tenant/user/conversation/event type as well. Existing events remain readable by scope
and payload; no consumer parses or reconstructs their IDs, so no data rewrite is
needed. Tests cover concurrent equal keys across all three identity dimensions,
binding rejection within each scope and recovery from all uncertain write states.
Verification: 77 tests passed with PostgreSQL enabled; one in-memory scope test is
not applicable and skipped. After the final audit-only simplification, 16 tool/
legacy-checkpoint tests pass again. Full repository collection: 1334 tests, no import
errors. This does not yet attest the whole suite or completion of legacy removal.
Retired the obsolete HTTP handoff fixture's AgentOrchestrator/ReAct imports and
private-global assembly. Its retry expectation created a second response, contrary
to Target's invocation/publication idempotency. Target cutover now checks OOS replay
with the same response and no worker/tool execution; the governed handoff test checks
receipt replay with exactly one ticket-port invocation; approval waiting has no action
receipt. Ticket priority and bounded active-case projection tests remain in place.
All 30 focused tests pass with real PostgreSQL enabled. This is a test-consumer
migration, not closure of the remaining ChatApplication/ReAct/SQLite removal.
Existing unrelated RAG working-tree changes are excluded from this migration's commits.
Exception explicitly authorized by user: RAG migrations 0030/0031 and their schema-version
tests committed separately as the linear migration prerequisite (ff22710).

Verification workspace: /tmp/dialogpilot-clean-runtime-qSXCb1 (detached committed snapshot).
The shared working tree's existing RAG policy/Bundle schema mismatch remains separate;
it is not hidden by an application fallback or by altering the startup test's policy.

Acceptance: correction during model execution cannot start stale tools; completed tool
steps survive recovery; concurrent users remain isolated; actual model/tool call counts
are bounded; each model request fits its context budget without changing stored evidence.
