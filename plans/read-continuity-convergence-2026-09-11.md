# Read continuity convergence

Status: owner-level product repair implemented and independently reviewed; local
validation passed. This repair is being committed separately from evaluator work.
Fresh paid task20 validation has not been run for this implementation.

## Current contract and convergence repair (2026-09-11)

The authoritative reusable read snapshot now belongs to the existing controlled
ToolManager boundary backed by LangGraph Store, not a private WorkItem checkpoint.
Native working messages and progress remain private to their legitimate goal.
All Target direct, framework-domain, Skill and write paths use that same boundary.
New Planner control/work-item IDs therefore do not discard reusable tool results.
The previous native reusable_reads reducer and Runtime read-epoch transport have
been removed, rather than retained as a second working cache path.
Deployment keeps existing working messages/progress but does not import the old
task-private cache as a competing authority. The new Store starts cold for those
historical reads; subsequent accepted reads populate it normally. No business write
is replayed to migrate cache data.

Only explicitly opted-in successful, accepted outputs may be reused. Identity
includes conversation subject, agent principal, tool/schema/registry versions and
arguments. Permission and argument checks run before lookup. Original observation
time and provenance survive replay; tool/Registry freshness and explicit refresh
determine when a new backend read is required. Private reasoning and completion
decisions are not inferred from cached results or shared between unrelated goals.

Writes invalidate the shared conversation snapshot epoch at the tool boundary.
Unique nonexpiring writer markers prevent overlapping or unknown writes from
enabling stale reuse. Validated governed reconciliation resolves an unknown marker;
ordinary cache TTL does not. Subject deletion fences cover lookup, persistence and
reconciliation. Cache maintenance failure after a committed write cannot erase its
receipt. The service is an adapter over existing Store, not a new execution engine.

The first detailed response failure is separately evidenced by Langfuse observation
152446afc5a316b6 (trace 8e4ae550704583724713600e3f5d9963): normalized output was
submit_claim_checks({}), with no assessment. This was not a semantic rejection of
the user's supplied information. A second empty output occurred in observation
a001406ceebf3fe4. Available evidence does not distinguish model from gateway cause.
The verifier now distinguishes INVALID_MODEL_OUTPUT from invalid caller contracts.
SDK Runnable retry makes at most two attempts for malformed structured output,
using the same evidence and no business tools. Valid REJECT, refusal and truncation
do not receive that retry. Exhaustion remains UNKNOWN, never implicit PASS.
With the existing single semantic revision, the maximum is four verifier calls.
Response assembly emits one verification-unavailable notice per turn and retains
recorded outcomes, rather than repeating the notice for each work item.

Acceptance covers generated scope/outcome sequences, actual compiler fresh-control
transitions, new task and continuation, explicit refresh, quarantine, concurrent
writes, unknown outcome reconciliation, deletion and store outages. PostgreSQL
tests close/reopen the Store across direct-to-domain execution and verify unknown
write markers survive startup TTL migration and sweeping. Independent fresh-context
reviews covered both the tool owner and verifier paths; review findings were fixed
with regression witnesses, including the reconciliation deletion race.

Non-goals: no new workflow framework, no goal merging to obtain caching, no increase
to task step limits, no paid benchmark rerun, and no assertion that model tool
emissions or all customer-service behavior are now optimal. Fresh real-model
efficiency remains a separate acceptance gate. The old results below are historical.

Final current-snapshot validation: **473 passed, 2 skipped, 4 warnings** with the
dedicated PostgreSQL test instance; 15 suites cover read reuse, task continuity,
framework Agent, orchestration, compaction, observations, progress, tau tool binding,
write workflows, structured transport, verifier, responses, tool security, claims
and result archive lifecycle. A separate conversation/action/catalog/tracing group
passed **154 tests, 2 skipped**. Independent verifier review ran 141 focused tests;
independent read-owner review ran 16 before the final deletion regression was added.
No fresh paid model task was used as an implementation gate. Next evidence step:
fixed task20, report external backend reads separately from model tool emissions,
and retain any invalid verifier output and official scoring outcome.

## Historical implementation and acceptance (superseded ownership)

## Reopened: causal-join-v3 implementation

The real Planner created new controls rather than the continuation/recovery
relationships exercised by earlier tests. Fifteen reads repeated with
NO_PRIOR_RESULT (9 then 6). First response verification also failed and prompted
user repetition. Prior local acceptance is historical, not closure of this scope.
Active work: audit and repair successful read reuse across actual new tasks and
the first response verification failure, including producer/archive/dispatch/
consumer boundaries. Independently review and validate state sequences before
another paid benchmark. Private goal progress must remain distinct from reusable
conversation-scoped read results; do not merge arbitrary goals to obtain caching.

## Evidence and scope

Task20 has 19 repeated reads: 6 after user supplementation, 7 within the
same execution, 6 after recovery. Native model observations show retained
comparisons but generic historical/staleness warnings prompting refreshes.
Progress detection is post-call and its graph fields do not cross continuation.
These are separate mechanisms, not evidence that every compaction clears state.

## Target contract

Existing framework state owns per-task read reuse and progress. The application
transports that state through accepted continuation/recovery boundaries, without
putting the ledger into model messages. Tool ownership defines reuse lifetime;
permissions, expiry and mutations determine eligibility, not the summary model.
Only successful explicitly reusable reads may bypass another remote request.
Reuse preserves original provenance and does not manufacture new evidence.
Different goals/identities must not inherit private working state accidentally.
Summary preserves choices, remaining work and existing freshness facts; being
summarized alone does not invalidate a result. Writes retain their own prechecks.

## Work

1. implemented/reviewed: audit all shared domain composition, context producers,
   continuation/recovery, result persistence and tool execution boundaries.
2. implemented: implement the smallest coherent read reuse/progress contract and
   migrate consumers; remove conflicting context wording.
3. locally verified: property/state-sequence and framework integration tests including
   compression, continuation, recovery, expiry, failure and scope isolation.
4. independently reviewed: independent review and documentation reconciliation. Report verified
   implementation separately from fresh model/benchmark closure.

## Implemented ownership and bounded scope

- `AgentResult.working_state` transports private native progress separately from
  lossy working messages. Accepted continuation and same-owner revised recovery
  retain observed identities, reusable sources and failed attempts, not the old
  stop latch or old action proposal.
- `ReadReusePolicy` is explicitly tool-owned and additionally bounded by Registry
  freshness. The framework restores successful archived results before backend
  invocation, through the existing permission/schema/control gates. Refresh,
  expiry, changed identity/version and governed mutation bypass the snapshot;
  opted-in reads do not fall through to a second generic cache.
- Runtime invalidates snapshots across observed writes and disables reuse during
  concurrent or unresolved writes, including results in the current plan.
- All six default model-loop domains share the same input adapter. Business
  observations enter only through assigned task/continuation/dependency IDs or
  explicit evidence sources. This projection preserves original archive pointers.
  The main conversation context remains conversation-scoped.
- Summary instructions preserve choices and actual freshness facts, without
  equating archival with expiry. Native read records stay outside model messages.
- The tau retail stable reads explicitly opt in; live/default tools do not gain
  indefinite reuse implicitly. Deterministic Skill execution is not a second
  model loop and is not claimed to have been tested as one here.

Independent read-only review found no demonstrated remaining blocker within this
scope; its requested current-plan unknown-write regression has been added.
Tests cover record-reducer permutations, scope isolation, cleared model history,
continuation/reassignment, explicit refresh and freshness bounds, and PostgreSQL
checkpoint/store close-and-reopen. Real-model reduction in tool emissions remains
unverified: avoiding a backend request alone does not prove token/step efficiency.

Delivery is still working-tree only. Concurrent tracing/evaluation edits overlap
the same files; unrelated work must not be swept into a commit. No fresh paid tau
run or claim that the historical task20 result has changed is made here.

Final local acceptance: **184 passed, 1 warning**, with the dedicated PostgreSQL
test instance enabled. Suites: task_read_continuity, target_framework_agent,
target_orchestration_runtime, target_context_compaction,
business_observation_continuity, execution_progress, tau3_tool_binding.
`git diff --check` passed. One earlier test assumed unconditional delivery of
unassigned observations; it now explicitly assigns its evidence source and still
checks historical observations do not become verified facts or approval.

### Causal attribution instrumentation

Implemented locally, not yet fresh-benchmark validated: successful read results retain
private source call IDs through result persistence; progress events join the consumed
read to the next emitted tool call; the tau3 adapter binds internal tool-call IDs to
official environment call IDs; and the reuse owner emits its pre-execution decision.
The offline probe attributes an exact repeated official call only when all four links
are present, otherwise it remains OPEN/CONDITIONAL. Verified causes are clustered by
owner, cause code and source path across tasks. Historical artifacts remain UNJOINED;
no evidence is retroactively invented.

Pre-rerun local acceptance covered direct reads, historical observation reads,
persisted artifact projection, repeated-model-call reuse, adapter binding and
synthetic joined RCA. The fresh run below supersedes the earlier no-rerun status.

### Fresh validation result

The user authorized one task20 rerun. The run passed official business checks but
falsified closure of the runtime reuse contract: 15 exact repeated external reads all
reached `tool_read_reuse` with `NO_PRIOR_RESULT`. The traces and official call bindings
join 15/15 repeats, so automated quality root status is `VERIFIED` and the same cause
is available for cross-task clustering. Nine repeats occurred in the work item that
hit the 22/20 tool-call limit; a later continuation recovered and completed the write.

The observed read keys remain identical across repeated requests, but each normal
semantic continuation starts a different work item without the previous reusable
record at the execution boundary. Existing continuation/recovery transport therefore
does not cover this planner-created lifecycle. The next product change belongs at the
application-owned task/continuation state handoff, not in the benchmark adapter or RCA
projection. No step-limit increase or task20-specific suppression counts as closure.

The instrumentation/evaluator repair is validated independently: per-call owner
decisions remain authoritative even when optional model-emission lineage is absent or
points to another consumed observation. Focused Python 3.12 acceptance is
`196 passed, 6 skipped`; the complete saved report is
`artifacts/eval/tau3-task20-causal-join-v3-2026-09-11/report.json`.

## Non-goals

No new agent loop, generic storage platform, default caching of every read,
blanket denial of refresh, increased step limits or task20-specific branches.
