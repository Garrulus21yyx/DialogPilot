# Read continuity convergence

Status: in_progress; no paid benchmark rerun authorized by this plan.

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

Local acceptance so far covers direct reads, historical observation reads, persisted
artifact projection, repeated-model-call reuse, adapter binding and synthetic joined
RCA. A paid tau3 rerun remains outside this plan.

## Non-goals

No new agent loop, generic storage platform, default caching of every read,
blanket denial of refresh, increased step limits or task20-specific branches.
