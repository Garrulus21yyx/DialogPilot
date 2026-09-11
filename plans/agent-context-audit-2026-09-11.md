# Whole-chain agent context audit

Base: a7c83c3; unrelated dirty evaluator files preserved.
Initial scope: read-only audit; subsequently authorized C1–C7 implementation below.

Status: audit and C1–C7 implementation complete; see repair validation below.
Implementation resumed by user request: owner-level repair, consumer migration,
property/regression checks and independent review. No paid business rerun.
Reference checked 2026-09-11: https://docs.langchain.com/oss/python/langchain/context-engineering
Distinguish persistent state from per-call model views; reuse existing middleware;
small role-specific context, not a second generic context framework. These are mature
engineering practices, not evidence of benchmark SOTA.
Independent worker and response-path reviews reconciled with the primary review.
The original audit phase made no production edits or paid model/business calls.

Criteria: clear authority; dialogue is not execution fact; no missing user restrictions,
goals, pending work or valid evidence; private domain messages remain private;
summary/read reuse do not fabricate freshness; composition and verification consume
role-specific views; complete provider request owns final token admission.

Findings must distinguish demonstrated defects, risks, and deliberate tradeoffs.
Tests/source inspection are not fresh model behavior or complete benchmark closure.

## Findings, ordered by semantic impact

### C1 — Loader drops messages before summary coverage is established

Owner/boundary: `infrastructure/target_turn_context.py:58`. The upstream reader can
recover PostgreSQL turns, but the loader selects the last eight messages without
checking whether its summary covers the removed messages. Planner compaction's
later coverage checks cannot recover content already removed by this loader.

Offline witness using the actual loader: summary covers sequence 10, available
messages cover 11–22, sequence 11 says “Only compare, do not purchase.” Output is
15–22, summary coverage remains 10, constraint is absent, projection stays READY.
This proves an input gap, not that this caused a particular historical task failure.

Target contract: window selection must account for summary coverage; an uncovered
tail must remain available or be summarized before removal. A summary is background,
not an authorization source. Do not solve this by simply increasing eight.

### C2 — Worker and its reviewer receive different coordination information

`application/orchestration_runtime.py:327` creates `assignment_view` with sibling
objectives, dependencies, states and retained results. The worker prompt at
`infrastructure/target_framework_agent.py:589` omits it. Domain instructions at
`application/agent_instructions.py:115` ask the worker to consider related sibling
operations; only the reviewer receives the view (`target_domain_outcome.py:138`).
An offline unique-marker probe confirms the sibling objective is absent from the
worker prompt. Actor/reviewer information asymmetry is established; historical
model behavior cannot be causally attributed to it without the specific trace.

Target contract: send bounded coordination facts to the assigned worker and reviewer
from the same assignment projection. Sibling context does not grant sibling authority.

### C3 — Authorized input evidence is confused with permission to produce evidence

Orchestrator supplies authorized dependency/continuation facts. At handback,
`target_framework_agent.py:699` filters those facts against tools this worker may
call. Offline witness: a policy fact enters a worker allowed only `order_lookup`;
an accepted COMPLETE returns SUCCEEDED with empty facts.

This proves handback loss, NOT universal publication loss: ResultBoard retains
predecessor results and merges their facts (`application/result_board.py:56,119`).
Per-item coverage and later continuation can still lose that input's association.

Target contract: distinguish newly produced facts (validate producer authority)
from already-authorized inputs (preserve provenance and required associations).
Do not broaden the worker's tool permissions to repair evidence transport.

### C4 — Product Skill changes source freshness

`infrastructure/target_product_execution.py:86` stamps a catalog-derived fact with
the current time instead of the ToolResult observation time. Offline witness:
catalog observed in 2020 becomes a fact observed in 2026. This is a proven provenance
conversion error, not proof of a real stale-data release.

Target contract: ToolResult→FactRecord preserves observation time/interval and
source; adaptation time cannot imply a refreshed business read.

### C5 — Assembler-to-composer context still contains planner instructions

Assembler captures one immutable evidence snapshot (`response_assembly.py:350`).
`composition_evidence` removes internal references but preserves
`user_context.observed_execution.contract/detail_contract` (`response_evidence.py:133`).
Their producer (`conversation_context.py:44`) instructs the recipient to choose
tools, repair objectives, delegate or resume. Composer has no tools and is asked to
write a final reply. Historical task20 observation `1dd75f4b86da267a` confirms those
instructions reached the composer, not merely that the source permits it.

Target contract: composer receives customer request, relevant dialogue, outcomes,
facts, pending actions/inputs and limited response instructions. Planner navigation
instructions are not reply evidence. Keep source snapshot binding; use role-specific
views rather than rebuilding independent business truth. Do not copy the verifier
projection wholesale: the two model roles have different information needs.

### C6 — Compose budget is checked before its actual model-input projection

`ConversationAgent.compose` (`conversation_agent.py:212`) checks the raw snapshot
before the provider projects it (`target_conversation_provider.py:48`). The configured
synthesis budget and final provider budget are also different admission points.
Offline witness: a removable 60,000-character source_ref causes 15,040 > 14,200
rejection with zero model calls. The same payload's actual rendered model input is
about 889 tokens and succeeds against a stub. This demonstrates false admission
failure, not its production incidence. Compose also lacks the planner's scoped
overflow recovery path.

Target contract: apply role projection first, then enforce the configured budget
against the complete actual request (system/messages/schema/output reserve).
Recovery may shrink/retry the failed model step, never repeat business writes.

### C7 — Repeated evidence expands the model views

The historical composer input above has 11 facts but only nine distinct facts;
its complete input is about 30,278 characters. Composer does not deduplicate them.
Verifier also receives knowledge packs through both its evidence-pack input and
the snapshot fact representation (`response_assembly.py:190,725`,
`services/answer_verifier.py:151`). Exact duplication is not additional support.

Target contract: one model-visible representation per exact evidence identity,
preserving outcome associations, public citations, temporal distinctions and the
unaltered audit snapshot. Similar-looking observations at different times are not
automatically duplicates.

## End-to-end context map

| Consumer | Actual input | Output / boundary |
|---|---|---|
| Context loader | Tenant/user/conversation-scoped transcript projection, summary, current state | Recent eight messages, summary, bindings and evidence; C1 occurs here |
| Encoder | Current message, up to six role/distance-tagged recent messages, objective features | Domain fast-path proposal or DEFER; pending work/approval/summary deliberately defer |
| Conversation.plan | Current user text, native recent user/assistant messages, bounded summary, pending input/approval, controls, observed results, capability descriptions and read tools | Global goals/control proposals or natural reply; no business write authority |
| Planner provider | Role-specific system, native messages and action schemas | Full-request budget and finite model-step overflow recovery; cannot repair loader losses |
| Orchestrator | Compiled WorkPlan, current/retained outcomes, dependencies and control state | Scoped AgentContextView, selected facts and private continuation state |
| Domain worker | Assigned objective/arguments/requirements, user message, background dialogue, selected facts/observations, decisions/receipts and its own native work history | Tool calls and domain outcome; C2 input omission, C3 handback filtering |
| Tools / Skills | Runtime identity, permission envelope, business arguments, shared read-reuse state | ToolResult/artifact or Skill result; C4 is a Skill conversion bug |
| Worker compression | Native message groups, protected current task/recent batches, archived original results and source directory | Local summary plus retained messages; not a second global conversation truth |
| Domain outcome reviewer | Proposed outcome and task/evidence/coordination context | Domain review feedback; currently sees coordination data actor lacks |
| ResultBoard | Paired current and retained WorkItem/AgentResult records | Deterministic coverage/conflict/delivery projection, not a new source of business facts |
| Assembler | Board, persistent pending actions/inputs, conversation context | Immutable evidence snapshot, selected delivery mode and optional composition payload |
| Conversation.compose | Projected snapshot, domain notes marked non-factual, response requirements | Natural customer-facing text; C5–C7 affect this boundary |
| Verifier, when required | Candidate, projected original evidence and requested knowledge support | Supported/answered/issues assessment bound to original snapshot; no authorization or execution |
| Publication | Accepted final text, assessment/source checks, current conversation/control state | Durable response; checks identity/hash/version, does not ask another model to rewrite |

## Every default domain checked

All six use the shared TargetFrameworkAgent/model-context path with distinct
descriptions, business policy and tool permissions. The τ retail environment uses
its own registry; it is not evidence that the default registry exposes retail tools.

| Domain | Primary allowed capability | Context-specific distinction |
|---|---|---|
| general | Knowledge, historical episodes, media | Shared worker path |
| product_technical | Catalog, media, knowledge, episodes | product_identification can invoke deterministic executor; C4 |
| order_logistics | Order reads, cancellation/address actions, episodes/media | No default knowledge_search; C3 relevant to dependency policy facts |
| billing_refund | Refund eligibility/status/action, order, knowledge/media | Shared worker path |
| account_security | Account events/state/freeze, episodes/media | No default knowledge_search |
| human_service | Support ticket creation/status | Narrowest tool scope, shared worker path |

No separate hidden last-N truncation was found in individual domain workers.
Summary reaches workers through recent_relevant_turns; the confirmed early loss is
in the loader. Arbitrary sibling private tool histories are not broadcast.

## Existing correct boundaries to retain

- Native user/assistant/tool roles are used; complete tool-call groups are preserved
  during worker compression. Source records remain separate from generated summaries.
- Shared read reuse is outside lossy model history and checks subject/principal,
  identity/freshness and write invalidation. This audit did not rerun its real task.
- Continuation restores selected same-owner/control work state, not arbitrary old
  goals, consumed approvals, budget counters or a stale stop latch.
- Composer is text-only. Domain notes and repair feedback are not business evidence.
- A valid verifier rejection permits one same-evidence rewrite; unknown/invalid
  verification is not fabricated as a valid rejection. Ordinary bound questions
  and deterministic execution status have non-model-review paths.
- Assessment and publication bind the original evidence/text rather than letting
  a compacted model view become authoritative.

## Risks/open contracts, not demonstrated business failures

- AgentContextView.token_budget is transported but the worker uses the shared
  composition budget. Clarify which configuration is authoritative before changing it.
- Worker fact prompts omit source_kind/producer/observation interval. No active
  USER_ASSERTED producer was found, so this audit does not claim a trust escalation.
- An invalid verifier response is now observable, but the historical empty tool
  arguments still cannot be attributed to context complexity rather than provider
  behavior from these findings alone.

## Coherent repair and acceptance scope (recorded before implementation)

The shared mechanism is loss or excess at role-specific context conversion boundaries:
source history→window, assignment→actor/reviewer, input facts→handback, ToolResult→fact,
and immutable snapshot→composer/verifier/provider request. More prompt warnings or
larger token thresholds do not close those contracts.

1. Repair coverage and source-preserving conversion at the owners (C1–C4).
2. Define composer/reviewer views of the same snapshot; remove foreign-role
   instructions, exact duplicate evidence, and pre-projection budgeting (C5–C7).
3. Verify properties across varying sequence windows, domain permissions, DAG
   dependencies/retained outcomes, repeated evidence and long metadata. Preserve
   user limits, all unresolved goals, pending action scope, source timestamps and
   outcome→evidence links. Projection must not mutate source snapshots or authority.
4. Integrate continuation, partial delivery, model-step failure and publication
   tests, then run fixed business cases only when separately requested.

Non-goals: new memory platform, second runtime, new orchestration state machine,
per-business special cases, universal online judge, or extra summary model per result.

Evidence: static producer/consumer inspection; offline loader/worker/freshness/budget
witnesses; independent response-path regression run **106 passed, 24 skipped**.
Skipped database tests do not establish real PostgreSQL publication verification.
No claim that historical empty verifier output or all benchmark failures are resolved.
Original audit delivery: document only; pre-existing evaluator changes and artifacts untouched.

## Repair validation — 2026-09-11

### Implemented contract

- C1: Target now reads the existing PostgreSQL ThreadSummary owner directly, including
  all verified summary chunks. Canonical transcript joins the same conversation-event
  sequence and includes all unsummarized dialogue plus the recent window. Removed the
  Target dependency on legacy Redis summary/window input. Redis turn-sequence checkpoints
  are not mixed with PostgreSQL event-sequence coverage. All constructor consumers migrated,
  including the mixed-business evaluation adapter. No second readable history path remains
  in Target. Unavailable/conflicted summary means canonical transcript without claimed
  summary coverage, not invented historical authorization.
- C2: worker and reviewer receive assignment coordination; it remains in pinned current
  task context through local compression. It does not broaden tool permissions.
- C3: authorized dependency facts survive handback with provenance intact. Requirements
  decide completion, not whether support evidence may travel. New tool/Skill output still
  requires producer authority.
- C4: original observation time and interval survive Product conversion. Missing time
  cannot be replaced with now. Direct/worker isolate invalid evidence while retaining
  valid progress; preparation returns typed unavailable evidence. Known committed write
  and reconciliation receipts remain COMMITTED even if the optional returned state cannot
  become a timed fact. No business replay is introduced for this condition.
- C5/C7: shared reply-role projection removes planning instructions/catalog routing
  metadata, preserves business scope and deduplicates exact facts with index remapping.
  Composer then removes private display references; verifier retains its evidence view.
  Duplicate knowledge packs reference their existing snapshot fact, with citation scope
  retained. Original snapshots remain immutable and bind verification/publication.
- C6: synthesis configuration is injected into the provider, not applied to raw internal
  snapshots. The complete projected request owns admission, including system and actual
  output reserve. Existing bounded model recovery retries only the failed model stage.
  Old dialogue can use the existing SDK-backed summary helper; facts, actions and latest
  context are not silently deleted. Null history and irreducible input retain typed failure.
  Summary counting for this path uses the same provider-envelope counter. At most two
  recovery rounds; each helper has at most eight summary calls, so at most sixteen summary
  calls total on this failure path (usually zero), never unbounded tool/business retries.

### Verification and delivery evidence

Follow-up: `plans/conversation-cache-consistency.md` describes the version-fenced
Redis cache added over this canonical PostgreSQL reader. It does not restore the
legacy Redis window/summary as a Target model-history source.

- New cross-boundary property/adversarial suite: `tests/test_agent_context_contract.py`,
  28 cases, including uncovered windows, sibling context after working-message assembly,
  cross-authority evidence, missing-time consumers, immutable role projections and budget
  recovery. Independent reviewer also ran all 28 successfully.
- Response/compaction/presentation/approval/verification regression selection: 325 passed,
  2 database-dependent skips. Prior 140-test input/worker/provider selection also passed;
  counts overlap and must not be summed as unique tests.
- Final combined input/worker/response/compaction/verification selection after all edits:
  **434 passed, 6 skipped**. These skips require a database; the separate real PostgreSQL
  selection above validates its explicitly listed scope.
- Real isolated PostgreSQL selection: 40 passed (memory projection, ThreadSummary,
  Publication, Product HTTP integration). Includes 205+ uncovered messages and event/turn
  sequence divergence. Fixtures create isolated databases, not production business writes.
- Broader identical selection on baseline a7c83c3 and current code:
  `tests/test_target*.py tests/test_*response*.py`: both 966 passed, 42 failed, 23 skipped.
  Exact failed test identity sets match: no added or removed failures. XML evidence in
  `/tmp/dialogpilot-context-baseline.xml` and `/tmp/dialogpilot-context-current.xml`.
  Baseline ran in isolated worktree `/tmp/dialogpilot-context-baseline-20260911-a7c83c3`.
  The 42 failures remain open; examples include stale handoff/state and read-reuse test
  doubles, direct-context expectations, read capability and conversation-response contracts.
  Matching failure identities do not establish correctness of those existing paths.
- Two independent fresh-context reviews found no remaining demonstrated C1–C7 blockers
  after consumer fixes. `git diff --check` passed. No fresh paid model or τ task was run;
  historical empty verifier output and end-to-end business quality are not declared solved.
- Scope exclusions: unrelated RCA evaluator edits/artifacts preserved and excluded from
  this delivery. No new runtime, memory platform, universal judge or per-business branches.
- Delivery: final regression passed; this document is delivered with the scoped context
  repair commit. Unrelated dirty files are deliberately not part of that commit.
