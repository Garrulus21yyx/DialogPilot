# Conversation capability execution convergence

Status: repair in progress; direct capability exposure and observation continuation
implemented with deterministic and PostgreSQL integration evidence. Real-model
goal preservation and remaining baseline failures are not verified closed.
Scope: failures from the fixed ten-task run after the rejected encoder trial.
Baseline evidence: `artifacts/eval/tau3-new10-after-encoder-trial-2026-09-08/`.
Current inspection HEAD: 6ee765f, with existing user-owned business recovery changes.

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

### PostgreSQL connection/runtime recreation and HTTP regression

At HEAD 9685d78, added three real PostgreSQL tests with a fresh isolated database.
Each closes the PostgreSQL pool and checkpointer, then constructs new instances of
the state store, manager and both graphs. Interruptions occur after the first read,
after the second read, or before final delivery. All restore the two paired results,
the committed conversation-state fingerprint and the final response, with exactly
two actual read calls and three planning decisions. This is connection/runtime
recreation, not an operating-system process-kill test.

The first PostgreSQL run passed 38 tests but exposed serializer warnings: trusted
context was annotated Mapping[str, str] despite carrying the structured knowledge
filter contract. Updated its existing AgentContextView, graph state and runtime
ports to Mapping[str, object]; an explicit warning-as-error serialization test
checks nested values and the actual checkpointed mapping without expanding the
checkpoint class allowlist.

The wider HTTP regression initially failed before planning because its fixture
connected an old text-classifier artifact (tuple predictions) to the current domain
encoder port (structured predictions). Its assertions already expected Conversation
Agent planning. Removed that obsolete fixture wiring, retaining current encoder-off
behavior; no runtime fallback or compatibility branch was introduced. Existing
user-owned business-recovery changes in the HTTP test remain separate.

Final PostgreSQL-enabled suite: 107 passed, no skips or warnings (observation PG,
domain approval, turn runtime, real HTTP/PostgreSQL scenarios, observation graph,
orchestration runtime). The isolated databases were cleaned by the existing fixture.
The stale permanent-provider-failure test was also migrated to assert the existing
PlanningUnavailable/no-accepted-plan contract, without changing production failure
behavior; its targeted suite passed 77 with 5 PostgreSQL skips when run without
the DB environment. These are distinct runs, not additive benchmark scores.

Still open: combined native-read→business-action approval/restart coverage, richer
queued observation obligations, and fixed real-model/task replays proving semantic
goal preservation and the remaining baseline repairs. No τ³/model run or encoder
activation occurred in this stage.

### Combined observed read and approved action recovery

Delivery of the preceding PostgreSQL verification: committed and pushed 066e488.
Added a combined integration witness in test_conversation_observation_postgres.py:
the main planner reads an order through TargetToolExecutor, receives that result,
delegates the original cancellation objective to create_agent, and waits for exact
action approval. After closing and recreating the PostgreSQL pool, checkpoint owner,
result Store, manager and agents, approval executes the governed write and resumes
the domain objective. Replaying that approval invocation does not repeat the write
or model calls. Assertions cover retained first-turn outcomes, no pre-approval
write, consumed pending approval, committed Receipt, final request completion and
identical replayed assembly.

Initial test-fixture failures were an oversized tool allowlist and a fresh memory
result archive after recreation. The fixture now uses exactly its registered tools
and the existing production PostgreSQL Store wiring; no production permission,
archive fallback or runtime branch changed. The final PostgreSQL-enabled combined
suite (observation recovery and domain approval) passed 24 tests in 27.01 seconds.
This uses scripted models and a counted external-write stub with the real workflow
ledger, not a real commerce backend or an OS process kill.

Still open: queued/mixed-task observation obligations and semantic goal preservation
under the fixed real-model baseline replay. Encoder remains rejected and disabled;
the original ten-task evaluation is unchanged. The new integration witness closes
the combined-boundary test gap, not the full task or architecture convergence claim.
### Mixed waiting and dependent-work verification

Hypothesis under investigation: an independent native read might not be observed
when another worker waits and has dependent work. This hypothesis did not reproduce.
ResultBoard produces explicit upstream-blocked outcomes for dependent work rather
than leaving absent results; the observation is therefore available, and existing
pending-state/resume handling restores the dependent chain after input arrives.

Extended the existing integration test into twelve combinations: zero/one/three
dependent steps, forward/reverse plan declaration order, and with/without an extra
main-agent read during the wait. Each asserts the original wait survives, the main
agent receives the independent reads, no read is repeated on user resume, dependent
work runs in order, and the request finishes. No production code changed based on
the speculative gap. Observation plus orchestration suite: 75 passed, three
PostgreSQL tests skipped in this non-DB run. Independent review requested for the
causal surface before treating this bounded verification gap as resolved.

Independent review falsified the broader claim for a different supported case:
the queued tail is itself a native DIRECT read. The original command sets an
observation obligation, but only WorkItem survives pending-state persistence and
it did not carry that obligation. Recompilation after user input silently used
observe_result=False. Eight added matrix cases reproduced the missing planner
call, despite the read itself completing successfully.

The positive contract is now attached to WorkItem: host-owned observe_result is a
boolean allowed only for direct reads, contributes to execution identity, survives
PostgreSQL payload and SDK checkpoint round trips, and is copied by state-bound
resume. RoutePolicy verifies it against the accepted continuation envelope.
TurnPlan's unconsumed observation IDs are projected from these WorkItems. No global
rule reclassifies all direct queries as observations. Legacy stored WorkItems
without the field need explicit migration; new turn checkpoints use runtime v10
and compiler v5, preventing silent replay under the changed contract.

The matrix now has 24 combinations, including native and delegated tails; added
false/true codec, fingerprint, resume and invalid-type tests. The focused non-DB
suite passed 132 with six DB skips. PostgreSQL regression and independent review
completed: 81 PostgreSQL-enabled tests passed with no skips, including observation
recreation, action approval, HTTP scenarios and persistence/manager tests. The
independent reviewer found no remaining concrete conversion omission in the
changed scope; internal evidence-only reads correctly retain observe_result=False.
This repairs a persistence ownership omission, not an LLM prompt
or a per-business special case. Original baseline model replays remain pending.

### Fixed task 19 repair replay registration

At fc25945, replay original retail train offset14/count1 (task19), once, using the
existing run_tau3_full runner and pinned /tmp/tau3.asUlWc checkout. Output:
artifacts/eval/tau3-task19-direct-observation-replay-2026-09-08/.
Hypothesis: the formerly rejected native identity lookup executes and its results
return to the main agent, which preserves the user's remaining business objective.
Fixed controls: existing role profiles, Flash user, seed300, max80steps, user512
tokens, no completion-budget override; encoder remains disabled. Budget one task,
no task replacement or automatic rerun. Measure accepted tool names, observation
continuation, actual actions, pending approvals, public replies, ALL/ENV/ACTION
separately and typed application/provider/evaluator failures. Missing judge is not
zero. Adoption requires no native-catalog rejection and faithful objective
continuation; full business success additionally requires supported final state
and consistent public completion. This is a development repair replay, not fresh
held-out evaluation. Current user-owned dirty modifications remain in the captured
source hashes, so end-to-end differences are not solely attributable to this patch.

Replay finished without restarting/replacing the task. Session 80160 exited zero;
task19 terminated max_steps (80). Official ALL/ENV/ACTION each assigned 0 for
premature termination, with db/action checks null: this is not a measured final-DB
mismatch. The model configurations matched baseline Worker and Verifier profiles.
Native find_user_id_by_name_zip and subsequent order/product/item reads executed;
there was no planning-invalid-provider-output/application exception in the local
error log. Thus the original native-catalog rejection is no longer reproduced.

The full task still failed. The trajectory records a committed water-bottle return,
then exchange_delivered_order_items rejected with "Non-delivered order cannot be
exchanged". Official policy requires delivered for both operations and changes the
whole order to return requested / exchange requested; exchange must gather all
items in one call. Yet earlier public replies promised both return and exchanges,
and the attempted exchange contained only the pet bed. Repeated confirmations
preceded the first prepared action. After rejection the public reply described
manual review / submission rather than plainly explaining the business rejection;
later reads repeated until the step limit. These observations require separating
policy/goal consistency, prepared-action authorization, and post-rejection outcome
presentation. No return/exchange-specific production condition has been added.

Next: trace the original goal, policy and prepared proposal through review and
approval, then trace ToolRejected through workflow recovery and reply evidence.
The relevant user-owned business-recovery edits are part of the recorded worktree;
inspect their contracts before modifying shared files. Do not run further tasks
to replace this failure or claim full closure from the successful read path.
