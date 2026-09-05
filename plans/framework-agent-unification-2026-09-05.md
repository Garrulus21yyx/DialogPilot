# Target framework Agent unification

Status: in_progress

Done: migrated remaining stateful coverage probes from legacy AgentOutcome /
CoverageGate to actual ResultBoard evaluations. Preserve frozen source datasets;
diagnostics explicitly identify the current owner and original legacy scenario.
Observe accepted prefixes and typed rejection of invalid result submissions, without
adding a production compatibility merger. Terminal completeness is separate from
successful outcomes. Removed services/result_synthesizer.py and orphan CoverageReport.
Retained TaskGraph dependency tests in test_legacy_task_contracts.py until the remaining
planning consumers migrate. Current ResultBoard tests cover every outcome status and
all permutations/prefixes of three independent results with invalid extensions.
Verification: 36 current runtime/retained TaskGraph tests, 17 PostgreSQL-enabled
stateful tests (100 original and 27 fresh cases), and five observation-probe tests pass.
No frozen sample changes; these runs prove current owner behavior, not reproduction
of the historical CoverageGate report schema or original runtime benchmark quality.

Done: retired orphan agents/task_policies.py and its exclusive tests after
removing the legacy Orchestrator and ResultSynthesizer consumers. Current WorkPlan,
LangGraph runtime, framework budgets and ResponseAssemblyPolicy own the supported
execution path. No old coalescing thresholds or task caps are copied into Target.
Verification: 40 current planning/runtime/context/response tests pass, covering
dependency waves, independent partial success and completion-order invariance.
All 20 framework Agent tests pass with PostgreSQL configured, including real restart
and model/tool budgets. This retires orphan policy code, not the remaining contracts.
The remaining CoverageGate fixtures require an explicit semantic migration:
ResultBoard.complete means all tasks have outcomes, not all outcomes succeeded.
Do not silently equate the two contracts or change frozen expected labels.

Done: removed orphan ReAct tool-context projection/compactor and the old
model-driven ResultSynthesizer. Keep only coverage/outcome contracts still consumed
by stateful fixtures until those consumers migrate to ResultBoard. Target response
assembly and framework context middleware remain the runtime owners; no new fallback
or summary engine is introduced.
49 tests pass with PostgreSQL configured across retained coverage contracts, framework
Agent/artifacts/restart, context budget, response assembly and stateful fixtures.
The retained legacy coverage module no longer contains a model client or synthesis
fallback; its fixture consumers are the next migration boundary.

Implemented: removed AgentOrchestrator (including its embedded BaseAgent/domain
classes) after confirming only its own test module imports it. Retire its old lexical
routing, fallback, scheduler and resume wrappers. Preserve independent shared
TaskPlan/Coverage/synthesizer tests separately; these shared consumers are not silently
deleted. Current Target tests own dispatch/dependencies, partial success, budgets,
control revision, approval/receipt and recovery acceptance. No frozen fixture refers
to the removed Orchestrator test functions. Historical review artifacts remain history.
The ReAct engine then had only its nine own tests as consumers, with no frozen fixture
references. Removed that engine and its provider-specific loop tests as well. Framework
Agent tests cover tool pairing/artifacts, optional skills, limits, revision checks and
context budgets; real PostgreSQL restart tests verify the selected execution owner.
Verification: 21 framework/restart/HTTP tests pass with PostgreSQL. Expanded suite
initially exposed missing database configuration for two stateful fixture tests;
rerunning with the configured test database passes all 82 tests, including the 100
stateful cases and held-out split. Full collection passes (1266 tests). The original
custom Orchestrator/BaseAgent/ReAct execution source is now gone. Remaining shared
legacy contracts/helpers and documentation still require dependency cleanup and a
final full-scope audit; this is not whole-goal closure.

Done: removed secondary legacy Orchestrator test consumers from Bundle,
classifier-mode and layered-dataset suites. Retire old constructor/threshold plumbing
and the circular claim that legacy generated routing labels define the current plan.
Preserve immutable PostgreSQL bundle tests, standalone classifier contracts, frozen
datasets/distributions and current Target plan/label-non-authority verification.
Also retired the old Orchestrator domain/instance plumbing test, retaining standalone
immutable policy tests until their owner migration. Only test_agent_orchestration now
imports AgentOrchestrator. Verification: 46 dataset/classifier/Target-planning tests,
eight real PostgreSQL bundle tests, and the focused policy/Target-runtime suite pass.

Done: removed the orphan command-primary chat bridge, work wrapper, result
projection and CLARIFY/OOS response adapters. Repository reference audit shows their
only consumers are within this retired cluster after ChatApplication deletion.
Target planning/ResultBoard/publication own these responsibilities; shared standalone
planning/evaluation modules are not removed until their remaining consumers migrate.
73 focused Target contract/runtime tests pass; one PostgreSQL test skips without
TEST_DATABASE_URL. Full collection passes (1316 tests). No old result or intent
projection adapter remains in this deleted cluster; legacy Agent consumers remain open.

Done: retired the final two legacy media chat harnesses and ChatApplication
itself after consumer audit. Shared ChatCommand/outcome contracts remain independent.
Current media authority lives at registered ToolManager calls, not pre-router media
injection; reads do not create a Flow. Add Target direct-media publication/replay
coverage and retain framework provenance and real upload/PostgreSQL tests.
35 focused tests pass with PostgreSQL configured, including actual asset upload,
catalog matching, Target publication and replay. Full collection passes (1316 tests).
Direct media currently publishes the tool-result JSON envelope; the new test proves
content/provenance preservation, not polished natural-language composition. Shared
legacy helper modules and AgentOrchestrator/ReAct consumers remain to be removed;
historical documentation references will need a final current-runtime link audit.

Done: retired the sticky legacy chat harness and its sole-consumer four-field
E2E scorer. Its legacy stage/intent/Flow projection is not the Target contract.
Preserve contextual refund reference binding, real business reads, same-request
publication replay, fresh-turn refresh and unchanged business state via the current
ChatApplicationRunner. No historical scorer or benchmark result is relabeled.
13 focused tests pass with PostgreSQL configured, including unchanged refund owner
state and the migrated locked counterfactuals. Full collection passes (1317 tests).
Conversation history and publication in the new contextual fixture are in-memory
test ports; the business state is real PostgreSQL. This does not claim a new durable
history/restart test. Remaining media harnesses still keep old ChatApplication alive.

Done: retired the legacy CLARIFY transport harness. Its publication/memory lists
were in-memory observations, not durable proof. Preserve frozen transport input,
non-scoring status, zero execution and same-publication replay using current Target
and ConversationAgent. Pre-task ambiguity has no bound WorkItem input signal; do not
carry the old draft pending-signal projection into the new contract.
The fixture now emits the supported asset_id missing field (the old composite label
is not a Target provider value); frozen inputs and score eligibility are unchanged.
18 focused tests pass, one PostgreSQL cutover test skips without its configured URL.
No durable-storage claim is made for this in-memory transport smoke.

Implemented: moved locked refund-eligibility counterfactuals to Target chat, framework
delegation and PostgreSQL publication/business owners. Seed initial business state,
preserve both tool facts, verify publication replay without new reads. Scripted
planning/model calls are contract fixtures, not quality scores. Remove old read-only
Flow side effects and the old harness.
Both frozen cases pass with real PostgreSQL state, business tools and Publication.
Replay uses the same committed response with one planning call and exactly two reads.
Removed the last legacy composition factory and its sole remaining old-mode test;
current Target OOS/provider-output and analytics-label non-authority gates remain.
Other directly assembled legacy test harnesses still require migration.
Verification: 50 focused semantic/planning/chat/data tests pass; one unrelated PG
cutover test skips in that command without its URL. The two migrated locked cases
pass separately with PostgreSQL enabled. Repository test collection also passes.

Done: removed test_command_primary_chat and its now-unconsumed structured
knowledge mode. Target knowledge/clarification behavior is verified through current
ConversationAgent, TargetChatApplication and HTTP PostgreSQL fixtures. Historical
reports retain old measurements with a retirement notice; they are not current
runtime documentation. The remaining structured read-only test composition is still
pending deletion, never a Target fallback.
Verification: 48 current semantic/chat/dataset tests pass (one database test initially
skipped); with PostgreSQL enabled, all seven HTTP Target and remaining structured
read-only convergence tests pass. Knowledge publication and typed clarification are
covered on the current chain; no legacy-vs-current score equivalence is claimed.

Completed deletion step: migrated legacy route-path application tests. Their label-driven
route dispatch and hand-written executor are retired semantics, not Target's WorkPlan
owner. Preserve the service-episode identity witness through create_agent and the real
registered tool. Existing Target compiler/runtime tests remain the positive gates for
direct, delegated, workflow, mixed/multi-domain and terminal no-work behavior.
61 focused tests pass, including completion-order invariance, dependency-scoped
context, independent partial success and real framework service-episode provenance.
One PostgreSQL restart test skips without TEST_DATABASE_URL; it is not counted as
fresh persistence proof in this stage. Remaining old application consumers stay open.

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

Target path has no old-engine fallback. SQLite implementations and their runtime
configuration have been removed; historical local database files are untouched.
Legacy in-process ChatApplication/Orchestrator/ReAct test consumers still remain,
so the single-chain migration is not complete.

Current step (done): retired the orphan LegacyIntentKnowledgeAdapter. The
removed legacy composition was its last producer; repository searches find no
remaining import or dynamic reference. Target-native encoder/cascade and planning
remain the supported route owners. All 22 focused tests pass across Target encoder,
fast-path policy, planning and semantic convergence, including the existing property
that every legacy analytics label leaves Target planning inputs unchanged. This is
not full-suite verification or closure of the remaining legacy runtime migration.

Done: retired test_chat_media_context's old private ChatApplication/TaskGraph
composition. Current media reads are explicit governed tools, scoped by tenant/user,
and can reuse an authorized prior upload; the old current-turn-only assertion is not
the supported contract. Verify OCR provenance and message authority through the real
framework/tool path, plus identity rejection before OCR. Keep tiered perception owner
tests for no-media/ OCR-only / missing-VLM outcomes; do not recreate old pre-router
media injection just to preserve the retired test shape. Target framework integration
now reads OCR through registered product tools, verifies ToolMessage placement and
MEDIA_OBSERVED facts with both tool-contract and OCR-producer versions. Separate tests
assert ToolManager quarantines instruction-bearing OCR and cross-tenant/user scope
errors occur before OCR. 25 focused tests pass, including frozen fixture references.
This proves boundaries with scripted providers, not general injection resistance or
a new live-model media quality claim.

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
Next cleanup removes the now-unreferenced SQLite tool execution table initialization,
claim/begin/complete/reconcile methods and locator reader. The legacy engine no longer
probes a result store with no producer. No SQLite files or stored rows are erased.
The unused RUN_STATUS_PROJECTION_CONTRACT string table and its self-referential test
are removed from admission. TargetRun's real outcome conversion is tested across the
public ChatOutcome union, including both waiting kinds, rejection of nonterminal
inputs and unknown persisted statuses. 48 focused tests pass. RunStore checkpoint
and legacy approval consumers are still pending, not covered by this removal claim.
Application boundary tests no longer instantiate old ChatApplication/ChatServices or
an old route-shape orchestrator. HTTP authentication/outcome mapping tests are retained;
Target cutover tests now cover unexpected runtime failure redaction and unavailable
history with both a complete current request and an unresolved historical reference.
The obsolete blanket memory-unavailable blocker is not copied into Target: unrelated
current-input work can run, unresolved references clarify without tool execution.
Existing Target planning tests own immutable registered plan/authority assertions.
23 focused tests pass, one PostgreSQL admission test skips without its database URL.
The locked L0 legacy evaluation runtime and other support harnesses are still consumers
of old ChatApplication and must be migrated before deleting that implementation.
Removed the orphan AgentOrchestrator.get_react_run/resume_react entrypoints and all
RunStore constructor forwarding through the legacy Agent pool/BaseAgent. The old
checkpoint's unused public-state serializer is also removed. Only the isolated
ReAct resume tests still construct SQLite RunStore; the old engine's optional
checkpoint implementation remains until those final gates are migrated. No automatic
fallback or replacement persistence layer was added. 81 focused tests pass; four
PostgreSQL-dependent bundle tests skip when TEST_DATABASE_URL is not set.
SQLite checkpoint implementation removal: deleted agents/run_store.py and the
dedicated legacy resume suite; removed optional RunStore/resume/checkpoint branches
from the remaining legacy loop. Its in-process consumers still need migration before
react_engine.py itself can be deleted. Retired the unused ReAct schema/code registry
and environment/Docker persistence settings. Old DB files and Docker volumes are not
deleted. Current recovery owners are Target Run, ConversationState/PendingInteraction,
OperationLedger and LangGraph PostgreSQL checkpoints, not a legacy fallback.
Verification gap retained explicitly: the removed RunStore credential-redaction test
only proved that retired SQLite serializer. It did not prove the framework checkpoint
privacy boundary. Audit and verify the current checkpoint input/serialization boundary
before declaring the full migration closed; do not count the old test as transferred.
Legacy file permission/schema tests concern the deleted storage format. Target approval,
operation recovery, stale controls and framework process-restart gates are retained.
After removal: 90 focused unit/contract tests pass; PostgreSQL recovery suite 74 passed,
one in-memory-only scope case skipped. Repository collection: 1340 tests, no import
errors. No sqlite3 imports or retired ReAct storage environment keys remain in code.
Checkpoint credential-boundary evidence: the existing PostgreSQL subprocess crash/
resume test now injects a runtime-only service credential, checks the tool receives
it, checks model messages exclude it, and inspects checkpoint JSON, blobs and writes
after restart. Tool-call content is present but the runtime credential is absent.
This proves runtime-context separation, not blanket redaction of user-supplied text
or business artifacts; that distinction remains explicit in the privacy audit.
Removed obsolete badcase/customer-operations/evolution Compose volume declarations
after confirming their services use PostgreSQL and no code consumes those paths.
No actual Docker volumes or database files are removed.
Input-security tests no longer import GeneralAgent/Request or inject the deleted
API _orchestrator. Blocked input is now checked against the Target application entry
itself, before admission or execution. Framework integration tests inspect actual
model messages for English/Chinese role-spoofing text: user content remains in a
HumanMessage, never in SystemMessage, and the exposed tool set remains read-only.
These are message/authority boundary tests with a scripted model, not a claim of
universal prompt-injection resistance. 44 tests pass; PostgreSQL restart test skips
without a configured database URL.
Retired legacy publication-feedback routing: removed ChatApplication's verification
callback, AgentStats EWMA/feedback counters and scores, and Orchestrator feedback/
monitor-penalty mutation methods. Retained execution availability/latency statistics.
The still-pending legacy instance-selection contract receives zero quality samples,
its registered prior and no monitor penalty; it is not another Target authority or
fallback. Its immutable policy types remain until legacy Orchestrator removal.
Monitoring regression now only checks observation of Target outcomes without routing
mutation; current runtime tests distinguish waiting from success-rate samples.
77 focused tests pass across legacy consumers, Target orchestration and HTTP adapters.
Retired the locked L0 legacy-only execution island: script, ChatApplication composition,
old-output scorer, report writer and dedicated legacy runtime tests are removed. This
is retirement, not a claimed port of its historical scores. Frozen 80-case data and
existing reports remain; the dataset suite preserves its 20-case L0 clarification
slice. The research entry now identifies retirement and the current Target evaluation
entrypoints. Existing Target CLARIFY/OOS/provider-failure tests and planning/evaluator
contracts remain the current behavior gates. No replacement compatibility runner added.
The dataset reference audit exposed an earlier migration omission: a frozen case
referenced the removed persistent-handoff test. The test identifier now contains a
real Target GovernedWriteRuntime + PostgreSQL OperationLedger/TicketService test,
including runtime reconstruction and receipt replay without another ticket call.
This restores the referenced behavioral witness without changing locked gold data
or adding an alias to the old runtime.
Verification: 35 focused tests pass with PostgreSQL configured; full repository
collection passes with 1332 tests. No new live-model benchmark score is claimed.
Removed the legacy off/shadow paired-chat harness and its no-change comparison test.
The legacy composition helper no longer supports off/shadow/legacy-intent modes or
accepts an unused Orchestrator dependency. Its explicit structured modes remain only
for the remaining old test consumers; Target has no mode switch to this helper.
Removed the obsolete production .env migration switch. Current Target planning and
semantic terminal tests remain the authority gates, not old-vs-shadow equivalence.
Verification: 11 focused tests pass, two PostgreSQL fixture tests skip without their
database URL. Repository collection passes with 1331 tests; this is not full-suite
runtime verification or closure of the remaining legacy test compositions.
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
