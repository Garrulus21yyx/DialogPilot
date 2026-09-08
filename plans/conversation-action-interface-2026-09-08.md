# Conversation action interface convergence

Base: d2287a2. User authorizes replacing the broad model-facing turn-plan protocol,
not adding a routing model or a second executable runtime. R01 remains semantically open.

## Evidence and cause

The retained 112-call experiment rejects guidance candidates. The provider currently
forces submit_turn_plan with every goal/state field; respond creates no commands.
This proves the failure location, not that schema simplification guarantees good judgment.
The internal compiler already owns binding checks, goal dependency and Registry commands.
Keep that owner; change the model-facing conversion boundary only.

## Positive contract

- One global model invocation returns native action calls or a conversational reply.
- Current application capabilities and state determine exposed actions. No pending approval
  means no review action; no resumable target means no resume action. General capabilities remain.
- Single state-owned identity is injected; multiple objects are selected from scoped candidates.
- All calls are collected and validated before the existing compiler/RoutePolicy/TaskGraph runs.
  Mixed approval, partial input and independent work retain one atomic proposal. Dependencies survive.
- SDK owns tool argument parsing, callback propagation and model transport. No new execution loop.
- No production fallback to submit_turn_plan. Old captures stay readable as historical evidence.
- Direct work never triggers domain replanning; open objectives use the existing domain worker.
- Knowledge query/options keep their existing temporal semantics during the transport migration;
  provenance limitations remain explicit, not silently replaced by current-time retrieval.

## Work

1. done: relevant producer/consumer review, installed SDK and official practice checks,
   independent fresh-context review required by repeated-reopen policy.
2. done: implement state-scoped action catalog/conversion and replace provider transport;
   migrate current capture/replay and provider test consumers. Preserve internal compile contracts.
3. done: generative state availability, call-set/dependency and source binding checks,
   SDK wire/callback tests and relevant integration/regression checks. No paid model calls yet.
4. done implementation/checks: independent implementation review passed after repairing delegation source
   selections/strict JSON and preserving all knowledge shortcut owners. Report added at
   docs/conversation-action-interface-2026-09-08.zh-CN.md; isolated staged-tree checks passed.
   This coherent change is delivered in its own commit; actual commit/push result is recorded in handoff.

Non-goals: new router, new agent runtime, RAG ranking, automatic retries, tuning, unrelated dirty files.
Implementation delivery is not semantic closure. A new paid acceptance requires a separately frozen
dataset/budget and full query-argument review; no more unregistered prompt experiments.

Baseline audit: clean d2287a2 reproduces three existing failures (shortcut test missing
continuation, option-length test missing required query, EntityBinding annotation missing
import). Corrected those assertions/import without relaxing runtime contracts. Initial
288 checks passed. Expanded checks identified these baseline issues and one new test fixture
with an invalid reasoning budget; model policy remained unchanged. Final counts follow.

Final verification: isolated staged tree (without unrelated worktree edits), 525 passed,
7 PostgreSQL-dependent tests skipped, 13.80s. Three SDK warnings belong to the separate
structured-output tests, not native planning. CLI --help and staged diff checks passed.
No paid models, business tools, deployment or checkpoint data migration performed.

Reproduce using `.venv/bin/python -m pytest -q` on these modules:
test_conversation_actions, test_conversation_agent, test_conversation_planning_schema,
test_planning_current_turn, test_structured_model_transport, test_compound_approval_planning,
test_conversation_plan_replay, test_planning_guidance_selection,
test_conversation_response_contract, test_parameter_acceptance, test_open_domain_delegation,
test_dynamic_knowledge_filters, test_sales_channel_contract,
test_target_persistence_and_manager, test_langfuse_framework, test_turn_runtime,
test_planning_output_contract, test_planning_action_flag_contract (all under tests/, suffix .py).

Status: action-interface migration implemented/reviewed; R01 semantic closure and shared
knowledge-filter provenance remain open. Next valid quality gate is pre-registered fresh
model behavior, not more schema tests or an unbounded prompt/few-shot experiment.

## User-authorized real-model regression (2026-09-08)

Base e9fff7a. Frozen12: previous confirmation01–08 (all eight), plus development
09f69/46c0f/dfe698/control_condition. Original message/history/bindings/capabilities
copied byte-for-value from baseline captures, stored in native-actions-regression12
input. These are consumed regression cases, not fresh held-out data.
Hypothesis: native action selection retains social/clarification behavior and selects
evidence for ongoing questions without adding conditions. Same Flash/NONE,2048 output,
SDK retries0,12 calls total,one per input,sequential; no prompt edits/retries/paid judge,
no business tool execution. Existing current replay/provider/compiler only.
Acceptance: protocol and compiled commands valid; no writes/approval; confirmation01–04
and09f69/46c0f/control_condition need policy evidence with supplied conditions preserved;
confirmation05–06 need short conversational replies;07–08 clarify actual ambiguous subject.
dfe698 permits needed jurisdiction clarification or evidence search, but not claiming the
old assistant's procedure as verified or converting not-expired to a60-day claim.
Full query/options assessed, particularly invented dates and hard product filters.
Report each failure, raw inputs/outputs, tokens/latency; old/new pairing is diagnostic
only (historical baseline, not contemporaneous). Stop at12 regardless of outcome.

Result: completed12 calls, protocol/compiler12 valid; manual + independent review10 pass,
1 coverage uncertain(Malta surcharge omitted),1 fail(dfe698 unverified old-assistant procedure).
Initial primary11-pass count corrected after independent full-argument review. Seven required
knowledge actions selected, but only six queries clearly preserve full requirements.
Input/history/action/sourcehash audit passed.52 replay/action tests passed. No RAG execution,
business action, extra retry or fresh-heldout claim. Evidence/report:
docs/native-actions-regression12-2026-09-08.zh-CN.md and artifacts/eval/native-actions-regression12-2026-09-08/.
R01 remains open; next scope is evidence-vs-transcript authority and full query coverage,
not another prompt experiment. This delivery records testing only, no runtime repair.
