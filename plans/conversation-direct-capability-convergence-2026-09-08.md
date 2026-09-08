# Conversation capability execution convergence

Status: repair in progress; direct executor identity migration implemented, full
capability exposure and goal-continuation repair not complete or verified closed.
Scope: failures from the fixed ten-task run after the rejected encoder trial.
Baseline evidence: `artifacts/eval/tau3-new10-after-encoder-trial-2026-09-08/`.
Current inspection HEAD: 32984bb, with existing user-owned business recovery changes.

## Established causal chain

- Trigger: the user supplies identity details needed for a larger retail request.
- Observed mechanism: native model calls to `find_user_id_by_name_zip` are rejected
  by `action_proposal` because they are absent from its callable catalog.
- Shared cause: `ConversationAgent.plan` advertises domain execution policy and raw
  tool names, but `planning_actions` exposes fixed high-level actions only. The
  instructions and actual callable capability surface disagree.
- This is not evidence that the model cannot understand the conversation, nor
  permission to execute an unknown model-selected tool without validation.
- Existing DIRECT_TOOL compilation does not close the gap by itself: goal parsing
  uses a fixed kind catalog; the direct executor uses a six-domain principal map;
  a successful preliminary read does not automatically preserve unfinished goals.

## Positive target contract

The conversation agent may choose authorized atomic reads, without mandatory
delegation. Exact callable schemas come from the registered ToolManager tool,
intersected with the capability Registry. The application supplies execution
principal, effect, requirements and permissions. One validated WorkPlan reaches
the existing governed executor; no new tool runtime or benchmark-only exception.

An explicit standalone read may finish after its result is delivered. A preliminary
read for an unfinished business objective must retain that objective and a legal
continuation. Existing same-plan dependencies can feed read results to a delegated
investigation. Whether a further main-agent tool-selection step is required must
be resolved explicitly before implementing the continuation contract; exposing
tools alone is not proof of an autonomous multi-step main-agent loop.

Writes retain the existing action preparation, approval and Receipt mechanism.
Unknown tool names, invalid schemas and unavailable principals produce typed
failures before execution. Business identity is not inferred from invented IDs.

## Owners and consumers to migrate together

1. ToolManager schema/description authority and Registry capability permissions.
2. Conversation action catalog, native SDK binding, conversion and goal parsing.
3. RoutePolicy/TurnPlanCompiler and direct executor principal/permission envelope.
4. Result facts, dependencies, original objective and persisted continuation.
5. Reply/approval consumers and public delivery; they do not infer task completion.
6. Tests, trace attribution and reports, including exceptional and resumed paths.

## Separate failures still under investigation

- Task28 goal narrowing is confirmed at the model output, not merely inferred
  from the final answer. Langfuse GENERATION c6f266a687c132f3 (17:45:17Z) delegates
  authentication/order identification with allow_action_proposals=false. After
  the user requests execution, 92676bd16d7c5d2d (17:46:02Z) again delegates only
  authentication/status checks with that flag false. The executor faithfully
  receives a read-only envelope; the user's return/cancellation objective was
  already absent from the delegated task. Task-local SUCCEEDED then appears as
  public task_completed=true. Repair must address objective preservation and
  distinguish local completed work from the user's remaining outcome; do not
  override the executor's envelope or infer write permission from a tool result.
- Task23: successful preliminary investigation did not produce the requested
  business change. Check planning and projection-diagnostic input authority.
- Tasks21/30: repeated confirmations and empty public approval turns; inspect the
  same pending action through execution and delivery, accounting for user changes.
- Task30: ALL/ENV/ACTION were assigned zero for max_steps, with null actual checks.
  Do not invent a database mismatch root cause from this score.
- Missing official judge credentials are evaluation failures, not application
  failures or model scores of zero.

## Verification and delivery

- Property/integration coverage: arbitrary registered read tool names/domains;
  schemas and authority preserved; unauthorized inputs produce no effects;
  successful prerequisites do not finish dependent objectives; failure, cancellation
  and resume preserve the same scope; one approval refers to one prepared action.
- Preserve all ten baseline results. Register the scope/budget before model replay.
- Fresh-context independent review before any restored closure claim.
- Commit only this work's changes, preserving unrelated dirty files. No encoder
  retraining, threshold relaxation, reranker work or second execution runtime.

## Direct executor prerequisite (implemented, not full closure)

Removed the executor's six-domain principal map. Registry is a required injected
dependency, as for framework agents. Every tool call now carries the WorkItem's
allowed_tool_ids into the existing ToolManager enforcement. Migrated every known
constructor in production, evaluation and tests; no default Registry fallback.

Checks: 78 passed / 3 skipped across identity, result conversion, knowledge outcomes,
chat cutover, refund continuation and media context. Two additional real ToolManager
authorization checks bring the dedicated identity file to 20 passed. Four broader
read-capability tests still fail before tool execution due to scripted knowledge
goals missing resolved_query; executing their unchanged HEAD definitions reproduces
those failures. No production contract was relaxed to suppress them. PostgreSQL E2E
constructor migrations are not claimed verified by these in-process checks.

## Native atomic read connection (implemented; goal preservation still open)

ConversationToolCatalog projects the existing ToolManager definition for each
Registry-authorized domain/read pair. Tool schemas remain at their original root,
including $defs/$ref. Shared names bind distinct owners; collisions with protocol
actions receive stable aliases. The optional bind_read_goals metadata action links
atomic calls to existing goal/dependency names without consuming a WorkItem or
inserting control fields into business arguments. Standalone reads need no metadata.
Conversion emits the existing DIRECT_TOOL command, not a second execution path.

Supported boundary: tool schema is an object-form JSON schema, actual parameters
are JSON objects, and approved/approval_token remain reserved execution controls
rather than supported business parameters. Context-dependent schemas receive the
turn's knowledge_filter_contract. Registry/tool/requirement inconsistency is a
PlanningInvariantError before the model; malformed model parameters are typed
INVALID_PROVIDER_OUTPUT. No blanket AttributeError swallowing was added.

Independent fresh-context review found and prompted fixes for dynamic schema
context, jsonschema exception typing, reserved control-field loss, requirement
consistency and nonobject parameter/schema handling. Tests now cover native SDK
conversion, arbitrary tool names, shared owners, original root references, business
goal_id/depends_on fields, configuration mismatch, typed invalid inputs, complete
compile→read→dependent fact delivery, and invalid dependency algebra.

This does not implement read→observe→new main-agent choice or prove that the model
preserves the full user objective. Task23's original Langfuse planning observation
e93e24ee0b512b31 also confirms identity-only delegation with action proposals false;
the original three business changes were not in that delegated objective. Repair
and validation of that semantic/continuation boundary remain required before
rerunning the frozen business tasks or declaring the user goal complete.

## Main read observation continuation — reviewed implementation contract

Status: design reviewed, implementation pending. The current TurnRuntime is a
one-pass phase graph. OrchestrationRuntime rejects a different plan on the same
execution thread and only resumes interrupted threads; its checkpoint is not an
arbitrary next-step execution API. PreparedTurn/ManagedTurnResult currently occupy
one slot and compiler IDs have no planning-step scope. These account for why adding
an outer while loop would lose outcomes or collide/replay execution, not a reason
to deny main-agent tool use.

Decision: extend the existing checkpointed TurnRuntime, not add create_agent as a
second executor around the same WorkPlan. Its model decision still uses the same
ConversationAgent provider, RoutePolicy, compiler and governed tool executor.
Framework StateGraph owns looping/checkpoints; application code owns the transition
between an accepted plan, its observed results, and the next accepted plan.

Required owner changes, to land and verify together:

1. Accepted plan records which reads originated from main native calls and need
   observation. Do not infer that from DIRECT (which also includes shortcuts and
   preparation). Reads already passed to a dependent domain goal do not force a
   second global interpretation after that domain finishes.
2. A bounded planning-step identity lives under the same invocation. Compiler
   work/control identities and execution thread scope distinguish those steps;
   no fabricated user messages or new authorization identities.
3. Turn checkpoint retains the original request/context and prior paired
   (WorkItem, AgentResult) outcomes. Each accepted next plan is checkpointed before
   execution. Previous outcomes feed ResultBoard's existing retained-results
   mechanism, never the new ready queue.
4. Manager constructs next-decision inputs from the unchanged original request,
   scoped state and newly observed facts. It does not treat the just-completed
   read objective as the user's full objective. Approval/input waits retain their
   existing authoritative transition and are not consumed by this loop.
5. A final main response after observation is checked against the retained board,
   not the RESPONSE_ONLY_NO_STATE_CHANGE branch with board=None. Domain completion
   does not require a redundant main planning call.
6. Planning failure/budget exhaustion preserves successful facts and produces a
   typed incomplete outcome and partial reply. Read-step completion cannot imply
   the outstanding user request completed. No automatic tool/write retry.
7. Checkpoint version and codecs, state commits, outcome projection, diagnostics
   and response assembly migrate with the same lifecycle contract.

Falsifiable acceptance: read→read→final; read→delegate without another global
planning call after domain completion; same model goal IDs across steps; failure
after a successful read with partial delivery; replay from every graph boundary
without rerunning completed tools; preserved approval/input waits; budget stop
reported incomplete; no committed write repeated by continuation. Add independent
fresh-context review after implementation, then fixed bounded model replays.

Separate semantic correction remains necessary: delegation describes the full
requested domain outcome, not authentication alone. A false allow_action_proposals
from the primary planner is faithfully enforced downstream; changing ToolManager
to ignore that envelope is not a repair. Do not blindly remove that flag without
tracing approval policies and all consumers (including USER_COMMAND_SUFFICIENT).

Implementation progress: compiler now accepts a trusted nonnegative planning_step
and scopes every newly created WorkItem/control identity with that step under the
unchanged invocation. Same-step replay is stable; model goal names containing
step-like text do not select another namespace. Compiler version v4. Scope/control
tests: 50 passed; pre-existing conversation/action tests: 95 passed. This code is
not yet an enabled read-observation loop; phase state, retained outcome delivery,
checkpoint migration and restart tests remain pending and must land coherently.

### Observation provenance and retained-step inputs (implemented prerequisites)

Native atomic conversion now sets host-owned `observe_result`; the model does not
fill another control field. The compiler records terminal native read WorkItem IDs
in TurnPlan. Reads consumed by dependencies do not demand redundant global
planning. RoutePolicy checks read effect; TurnPlan validates membership, uniqueness,
direct/read mode and non-consumption. This provenance participates in plan identity
and the existing checkpoint codec. Exhaustive four-node forward DAG checks cover
all 64 dependency graphs, alongside shortcut/native distinction and invalid frontiers.

OrchestrationRuntime accepts prior paired outcomes when starting a distinct execution
step, evaluates them through the existing ResultBoard, and supplies their facts to
workers without scheduling the old items. Reopening that execution requires the
same observed outcomes. In-memory checkpoint recreation tests cover successful and
failed second steps, preserved first-step evidence, partial delivery, no tool replay,
and rejection of altered prior observations. This is not a real PostgreSQL restart
test or a main-agent loop test.

Checks: 175 passed, 3 skipped across conversation/action/compiler/control and
orchestration tests. Initial added test omitted the constructor's required
domain_workers argument; corrected the fixture, not the runtime contract. No model
or τ³ run. These prerequisites are implemented; TurnRuntime observation branches,
manager next-decision context, original-objective completion/failure projection and
end-to-end checkpoint migration remain open. Do not enable a loop or claim closure
from these component results.

### Checkpointed main observation chain (implementation and component validation)

TurnRuntime v9 now commits completed progress before a separate observation-planning
node. The same ConversationAgent receives the original user observations/history
and the paired execution board, bypassing state-signal resolution and the encoder.
PreparedTurn carries monotone planning_step; the existing compiler and runtime
scope execution identities, preserving earlier facts without scheduling their work.
Final response candidates use the accumulated board. The manager derives request
completion from both work outcomes and whether a main observation remains; reply
verification and public delivery consume that value rather than equating a
successful prerequisite with the whole request. Semantic delegation instructions
now explicitly retain the full requested domain outcome and conditional changes;
this prompt change still requires real-model verification.

Planning failures and exhausted observation budgets retain the prior board and
publishable partial results with distinct diagnostics. Invalid dependency algebra
is converted to TurnPlanningError at compilation, not caught as an arbitrary
runtime exception. CLARIFY retains missing fields alongside prior evidence in
composition and the existing public missing-input projection. Trusted state conflicts
and configuration invariant errors remain errors rather than candidate retries.

The earlier blanket stop for any pending input/approval is superseded: independent
completed reads can be observed while another goal waits, without presenting
approval/slot-consumption tools to the observation phase. Further independent work
uses the existing waiting checkpoint and explicit paired observed outcomes. The
runtime distinguishes accepted observation inputs from its derived accumulated
results when checking replay identity. No new user message is fabricated. The
first-turn presentation state is checkpointed separately from step-local execution
state so newly created waits remain visible even without a publication callback.
Manager.handle is a facade over the same TurnRuntime graph, not another lifecycle.

Validation: 336 passed, 10 skipped, 1 baseline contract mismatch in the wider
targeted suite. The mismatch is test_turn_commit_boundaries' permanent provider
failure case: it expects a failure TurnPlan, whereas HEAD already raises
PlanningUnavailable before a plan exists. The production failure branch is
unchanged; this is not reported as an entirely green suite. New observation tests
cover read/read/respond, read/delegate with no third planner call, typed invalid
dependency and provider failures, budget exhaustion, facts plus clarification,
mixed waiting plus another read and next-user resume, public outcome retention,
and injected interruption before observation, after child execution and before
assembly. The A-waiting/B-completed/C-resume path also has an orchestration test.
Independent reviewer ran atomic/observation tests: 48 passed, no model calls, and
found no further deterministic blocker in the reviewed bounded implementation.

Not closed: real PostgreSQL restart/write-approval boundary verification, richer
queued/mixed-task observation coverage, fixed model replays demonstrating preserved
business objectives, and remaining ten-task baseline failures. No fresh τ³ run,
encoder activation, training rerun or reranker work occurred. User-owned business
recovery and RAG edits remain outside this delivery.
