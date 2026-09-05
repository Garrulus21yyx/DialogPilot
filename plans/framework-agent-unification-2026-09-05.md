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

Next cutover surface: API startup still constructs AgentOrchestrator for routing-only
evaluation and legacy Skill/ToolManager injection. These remaining consumers must use
the Target planning boundary rather than recreating another semantic router.
Then migrate legacy evaluation harnesses and behavioral gates before removing old
ChatApplication, compatibility execution, ReActExecutionEngine and its RunStore.
ToolManager's optional execution_store claim branch remains another old RunStore
consumer; Target writes already use the PostgreSQL operation owner instead.
Existing unrelated RAG working-tree changes are excluded from this migration's commits.
Exception explicitly authorized by user: RAG migrations 0030/0031 and their schema-version
tests committed separately as the linear migration prerequisite (ff22710).

Verification workspace: /tmp/dialogpilot-clean-runtime-qSXCb1 (detached committed snapshot).
The shared working tree's existing RAG policy/Bundle schema mismatch remains separate;
it is not hidden by an application fallback or by altering the startup test's policy.

Acceptance: correction during model execution cannot start stale tools; completed tool
steps survive recovery; concurrent users remain isolated; actual model/tool call counts
are bounded; each model request fits its context budget without changing stored evidence.
