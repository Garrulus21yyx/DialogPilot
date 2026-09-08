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

### Task19 causal inspection: goal coupling and effect knowledge

Langfuse GENERATION inputs establish the first irreversible decision's scope:
a73c3a10b05110f5 accepted PREPARE_ACTION with an objective containing only the
water-bottle return. 7bafb86598e8d3b4 later accepted its completion and explicitly
treated exchanges as outside that objective. 97c7bbc05ac05f58 accepted a separate
pet-bed-only exchange. The domain review did receive business policy, but global
planning had split operations sharing the order's mutable state. Therefore simply
strengthening the local review would not restore the missing related objectives.

Implemented a small general instruction repair at the existing planning and
PREPARE_ACTION owners: keep changes sharing mutable object state or one-time
capabilities in one delegated objective; assess remaining-action feasibility and
policy-required batching before the first proposal; resolve incompatible choices
before committing to either. No tool names, case IDs, new model layer or new
workflow enumerations were introduced. Unit/contract regression does not prove
real-model uptake; semantic validation remains pending.

Independent review and code agree on a separate deterministic information loss:
ToolRejected preserves the explicit business error and NOT_COMMITTED call effect;
_ToolPort discards the error detail into TOOL_REJECTED; WriteToolOutcome and
OperationRecord retain no non-success observation; the intended MANUAL_REVIEW
handling policy then publishes UNCONFIRMED regardless of known effect. Task19's
turn6 public manual_review_actions confirms exactly this projection, with a real
ticket ID and zero recovery attempts. Manual escalation itself is intentional in
the user-owned recovery contract and should remain; removing it would change scope.

Next owner-level repair must separate handling status from effect knowledge in the
existing operation record, carry the error detail/source into the ticket, model
feedback and fallback, and preserve it on restart. Distinguish a rejected first
attempt from rejection of a retry after an unknown earlier attempt: the latter
does not establish that the entire operation never committed. Authoritative
operation reconciliation can settle prior uncertainty; a later attempt-local
error cannot. Legacy records cannot recover lost details and must not invent them.
Acceptance covers first rejection, unknown-then-rejection, authoritative operation
results, exhausted recovery, late responses/CAS, restart and ticket replay. This
remains open; no production recovery fields were changed in that inspection stage.

### Effect-knowledge repair in progress

The current working implementation carries detail/source from the tool adapter
into WriteToolOutcome and persists effect knowledge separately from manual handling
in OperationRecord. Attempt-local rejection after ambiguous execution does not
settle the original operation; operation-scoped reconciliation can. Ticket content,
execution feedback and fallback consume the recorded distinction. Legacy payloads
retain unknown knowledge rather than reconstructing discarded errors.

The new restart/codec/fallback tests cover first rejection, ambiguous replay
rejection and authoritative reconciliation, plus two adapter error descriptions.
Initial non-DB verification: 5 passed, 3 PostgreSQL cases skipped. A missing primary
WorkItem ID in the new test fixture was corrected; no production change was made
for that test error. PostgreSQL and exceptional-path verification remain pending.
These edits overlap the user's existing business-recovery work and are not yet
committed; unrelated work must not be staged with this repair. No new tau replay
has run, and this does not establish semantic or full-task closure.

Independent review identified the dispatch boundary as part of this same knowledge
contract: a new dispatch must invalidate a previous NOT_COMMITTED conclusion in
the EXECUTING CAS before I/O. Both initial/retry and recovery-replay reservations
now set UNKNOWN while retaining the last observation as historical evidence.
Cancellation-at-dispatch and 27 generated reconciliation histories exercise this.
Legacy COMMITTED records retain their authoritative receipt-backed COMMITTED
knowledge; only lossy non-success records default to UNKNOWN. Record validation
rejects non-enum effects and contradictory committed states, and the shared ledger
CAS rejects changes after committed termination.

First PostgreSQL-enabled recovery/workflow/projection/registered-owner suite:
146 passed, 2 skipped in 147.70 seconds. This run preceded the independent-review
dispatch/legacy validation changes and therefore does not validate those changes.
Subsequent non-DB effect/workflow/persistence suite: 75 passed, 54 skipped.
A PostgreSQL rerun of those revised boundaries completed: 128 passed, 2 skipped
in 80.38 seconds; both skips are database-scope isolation checks in the in-memory
fixture, not missing PostgreSQL coverage. Independent fresh-context read-only
review confirmed the dispatch/legacy/type/terminal fixes without another concrete
blocker in that surface. Diff whitespace checks passed. Implementation remains
uncommitted alongside overlapping user recovery changes; semantic task validation
and coherent delivery remain open. Production
reconciliation currently emits COMMITTED or UNKNOWN; injected operation-level
REJECTED tests establish the port contract, not a new production query capability.

### Coherent recovery delivery preparation

Reviewed and staged the existing bounded-recovery implementation together with the
effect-knowledge repair and its actual dependencies (Registry/WorkItem pinning,
ledger/checkpoint codec, tool conversion, manual ticket, state/public projection,
tests and reproduction script). Unrelated RAG, archive navigation, ACK additions
and resume documentation edits remain outside this delivery.

Exported index tree `1de3406bbd80a0e1bf57d46c95f4c95cb0f97be9` into a clean
temporary snapshot. PostgreSQL effect/recovery/projection/registered-owner/workflow/
persistence/HTTP suite: 169 passed, 2 in-memory-scope skips in 132.65 seconds.
An additional approval gate exposed pre-existing stale test doubles: direct reply
node calls lacked presentation_state, request_completed and route.missing_inputs.
The graph supplies these fields; production TurnRuntime has no diff from HEAD.
Updated only the test inputs, preserving their published/unpublished approval and
pending-input assertions. No production default or approval rule was relaxed.
The revised index tree `71e28a59df1f753d876e33e7867c2f12b0d4fc20` differs only in
that test and the historical-evidence documentation; all tested production code
is identical. Its non-DB architecture/tools/domain-approval/revision suite passed
60, skipped 19 PostgreSQL cases; the subsequent PostgreSQL-enabled run passed
all 79 tests in 33.10 seconds with no skips. Staged whitespace checks passed.

This delivery closes neither real-model goal coupling nor repeated confirmation.
The fixed10 and task19 failed trajectories remain unchanged, and Encoder stays off.

Delivery: implementation commit `1c0afc6` pushed successfully to
`origin/feat/customer-service-target-architecture`. The prior "uncommitted" entries
above describe earlier inspection stages, not the current delivery state.

Next causal inspection started from the unchanged task19 artifact, without a new
model run. Messages 16 and 28 ask the user to confirm all changes; user messages 17
and 29 assent. Yet application turns 2 and 3 are CONVERSATION_RESPONSE after native
read successes, not pending approvals. The first durable approval appears only in
turn4 (message34), and scopes the prepared water-bottle return, while the public
question again asks to proceed with all three changes. Thus at least these early
repetitions precede any approval-resume handling: the main reply solicits execution
confirmation before a prepared action exists. Later item-by-item approvals are
additionally coupled to the already-established split-goal problem. Do not treat
all repeats as stale approval consumption or accept unbound assent as authorization.
Next: inspect planning-response and composition inputs for the missing preparation
boundary, and verify how scope is presented from the actual prepared action. Keep
the existing single approval authority; no regex/business-specific confirmation gate.

Inspection at c8f7f22: ACTION_INTERACTION_CONTRACT is currently owned by the
infrastructure domain-review module and reused only by the domain SDK loop/reviewer.
Main planning lacks it; its instruction says delegate "only open investigations",
despite delegation also being the supported preparation path for open business
changes. Main composition has a partial parallel description, and direct planning
text can bypass composition. The shared boundary is therefore not consistently
described to all authors, even though runtime approval state is singular.

Target repair: maintain the existing resolve/prepare/approve contract in the
application approval owner and reuse it in main plan/compose and domain consumers.
Describe direct lookup versus domain business preparation accurately. Preparation
permission is not execution approval; information-only requests stay read-only;
scope of a confirmation must match the actual prepared proposal. No new semantic
model, regex gate, tool runtime or blanket prohibition of normal clarification.
Tests will check actual prompt wiring and unchanged action/state contracts; this
alone cannot prove real-model behavior. Keep semantic replay as a separate gate.

Implemented shared contract and main delegation description. Captured SDK model
inputs verify both main phases receive the same contract as domain review, with no
extra planning/compose invocation. The remaining direct reply-node test double in
test_native_response_contract was migrated to the same current presentation input
shape as the prior approval suite; search found no other direct test callers.
Independent review found no additional concrete responsibility/input gap, but
requires semantic validation of pre-proposal confirmation and proposal scope.
Clean staged tree bb9f6a1fc830069ea16c87b8703276c150cfc1de: 362 passed, 2 PostgreSQL
tests skipped in the non-DB run. No production reply verifier or approval grants
were loosened. Archive navigation edits remain unstaged.

Semantic replay registration: fixed task19, original retail train offset14/count1,
one trial, seed300, max80 steps, Flash simulator max512 tokens, existing Worker and
Verifier profiles, Encoder disabled. Output:
artifacts/eval/tau3-task19-shared-interaction-replay-2026-09-08/.
Hypothesis: the main agent prepares/delegates the whole coupled goal instead of
soliciting permission in ordinary text; approval wording covers only the actual
prepared action, with incompatible choices resolved before a write. Inspect first
proposal, each question/assent and corresponding durable state, actual write and
rejection outcomes, final response and official scores separately. No task
replacement/restart for a better score. This is a development regression, not a
held-out success rate. A failure remains evidence and cannot establish closure.

PostgreSQL rerun of the domain-outcome boundary suite completed: all 50 tests
passed in 6.46 seconds, including the two previously skipped cases.

Shared-contract implementation commit `2d06b66` was pushed. Fixed task19 replay
session 80027 completed with exit0, user_stop, six application turns. ENV=0 with
actual DB mismatch; ACTION=0 (all six required reads matched, required return write
absent); ALL unavailable due evaluator error. This differs from the prior max-step
zero with null checks: here no business write executed. The simulator produced
text and stopped normally; do not attribute this failure to an empty user message.

The semantic hypothesis did not pass. Turn1 sent the identical empty-email lookup
five times; every external result says User not found/error=true, and all five
application outcomes are TERMINAL_FAILURE/TOOL_REJECTED. Turns1/2 exhausted the
main observation budget. Turn3 again asked to proceed without a prepared action.
Turn4's direct reply denied previously known identity/order/pricing and failed
answer verification. Turn5 attempted delegation but supplied order=W2890441 where
the model schema required entity_1, causing CONVERSATION_PROVIDER_OUTPUT_INVALID.
No confirmation/preparation closure is established by the shared prompt migration.

No further task was launched or production branch added after this result. Asked
the existing independent reviewer to trace observation failure detail/progress and
entity-selection conversion end to end, distinguishing model noncompliance from
an actual input/contract defect. Next repair must account for this shared main-agent
execution boundary, not special-case empty email or accept arbitrary order values.
Original and new failed artifacts remain separate and unchanged. Encoder remains
disabled; the overall persistent objective remains open.

Observation-contract audit in progress at 0c99883. Independent review confirms
that TOOL_REJECTED feedback includes the error, but the conversation projection
omits the task arguments that produced it. Native read objectives contain only
`Read <tool>` and cannot recover those arguments. The projection owner will expose
the persisted task input paired with its outcome, including pinned DIRECT tool,
without projecting approval tokens or treating a delegated allowlist as an actual
tool trace. This is task input, not a claim about post-injection external arguments.
Verify successful, failed and unresolved outcomes and checkpoint round trips.
The separate no-progress execution contract and entity selector simplification
remain open; this input repair alone does not prove either semantic closure.

Implemented at the shared conversation projection. Eight combinations cover
DIRECT/DELEGATED and successful/terminal/retryable/unexecuted outcomes, nested and
empty arguments, immutable source data, checkpoint round trip and native model
message round trip. Independent fresh-context review found no concrete blocking
projection issue. Targeted observation, planning-current-turn, conversation-agent,
action-catalog and SDK-transport suites: 146 passed in 5.29s; three existing SDK
thinking/forced-structured-output warnings. No live model or tau task run.

Next execution contract must distinguish semantic request identity from random
per-step call IDs. It must preserve independent successful work, permit changed
arguments/new user input or relevant state changes, and not prohibit legitimate
fresh reads or treat an arbitrary conversation CAS increment as progress. Audit
the existing execution owner before choosing a typed no-progress transition;
do not add a broad caller-side exception or a permanent tool-result cache.

## Progress owner review at 3275d7c

Repository evidence: `AgentProgressMiddleware.abefore_model` already implements
two stagnant rounds, feedback, and one recovery round in the domain graph. Its
identity combines tool name and result digest but not native call arguments.
`TurnRuntime._plan_observation` has only a total observation budget. Thus there
are two different gaps: the main loop has no recovery feedback before budget
exhaustion; the domain identity cannot distinguish new parameters returning the
same error. Neither is an executor auto-retry. A Manager-level duplicate-command
exception would reject independent work in the same batch and is not the chosen
repair. An unconditional same-call ban would also reject genuine changed results.

Independent architecture review supports this bounded target:

- Share a pure progress transition (state + eligible new read observations ->
  state + continue/warn/stop), not a new scheduler or database. Retain current
  bounded recovery semantics; thresholds are budget policy, not correctness proof.
- Request identity uses tool, scoped owner and canonical arguments; outcome
  identity uses meaningful data/typed error. Exclude random call/work IDs and
  incidental CAS/observation timestamps. Changed arguments count as a new attempt;
  changed outcomes for the same request count as progress. Total budgets remain.
- Domain adapter pairs native AI tool calls and ToolMessages by call ID. Preserve
  existing per-evidence knowledge identity: recombining old sources is not new
  evidence. Main adapter consumes only the new DIRECT observations from the current
  execution batch, not cumulative retained outcomes or a delegated allowlist.
- Do not charge a non-read interaction/write batch as a stagnant read round.
  Mixed batches retain eligible independent read observations. Approval, pending
  input, writes and reconciliation retain their existing lifecycle owners.
- Existing graph state persists counters and consumed observations. A new user
  segment resets them; restoring the same checkpoint does not reset them or count
  the historical tail a second time.
- Domain stop retains results through AGENT_NO_PROGRESS. Main stop uses its
  existing partial-delivery path and a distinct diagnostic, preserving the board
  and pending interactions rather than declaring successful work failed.

Acceptance: generated sequences with changed/same parameters and outcomes,
reordered batches, mixed interactions, knowledge recombination, replay and fresh
user boundaries must produce consistent decisions in both graph adapters. Verify
retained successful results and unchanged write authority separately. Existing
progress + conversation-observation suites currently pass 62 tests in 5.84s;
those tests do not cover the full target above, so they are baseline evidence only.
No production progress change or live task replay has been made in this review.
Next action is implementation of this shared transition and both adapters, not
another prompt modification or a new benchmark trial. Selector and interaction
semantic failures remain separate open items.

Implementation in progress: shared `application/execution_progress.py` owns only
the pure transition and main read-observation projection. Domain middleware now
pairs arguments by native call ID, skips consumed calls, and does not discard a
read batch merely because an interaction is also present. Main TurnRuntime stores
progress in its existing checkpoint, passes warning feedback to the existing
planner, and reports OBSERVATION_NO_PROGRESS through retained-result delivery.
Runtime checkpoint version advances to v11; no old in-flight checkpoint is silently
reinterpreted. Result archive pointers retain whether request arguments participate
in knowledge novelty, matching inline evidence after compaction.

Initial tests: 81 passed, one PostgreSQL case skipped. Added generated transition
sequences, argument/result identity, mixed interaction/read, native-call replay,
main warning/recovery/stop and committed replay tests. Broader tests and independent
review are still running; no semantic task replay or closure claim.

Review corrections implemented: successful main knowledge observations now reuse
the knowledge owner's per-evidence identity; scheduler BLOCKED/CANCELLED/waiting
outcomes are not counted as executed reads. Random fact subject/call references
are excluded from business novelty. Independent re-review found no further concrete
blocking issue in this bounded progress implementation.

Final targeted run: 158 passed / 1 PostgreSQL test skipped in 6.72s. PostgreSQL-
enabled turn-runtime + observation suites then passed all 63 tests in 5.57s.
New main warning/recovery/stop and fresh-user tests use InMemorySaver with the
production serializer; committed invocation replay performs no extra tools.
Generated transition states also round-trip that serializer. These are not a new
OS-kill recovery test or a real-model effectiveness measurement. No tau replay.
Remaining causal work: entity selector contract, whole-goal preparation and actual
model response behavior. Encoder remains disabled. Selective commit excludes the
pre-existing framework/archive/RAG edits owned by the user.

## Entity selection simplification in progress at 66fa5b3

Observed task19 delegation supplied the actual known order W2890441 while the
native schema demanded entity_1. That output violated the advertised enum; the
conversion correctly refused it, but the extra value-to-alias translation is not
needed for unambiguous values. `_entity_choices` is the single owner used by both
shortcut and delegated action schemas. Simplify there, not by accepting arbitrary
strings in a downstream error handler. Exact scoped candidates remain an enum and
their source refs remain host-bound. Preserve explicit selectors for same-value
different-source candidates, avoid alias/value collisions, and deduplicate identical
value/source pairs. Migrate current scripted SDK callers; immutable historical
captures stay historical. Tests must cover source preservation, candidate ordering,
duplicates, conflicting sources and invalid selections before any execution.

Implemented the shared choice conversion and migrated both current scripted SDK
callers; provider contract version v22-scoped-entity-values. Generated permutations
cover shortcut/delegate order/media selection, identical duplicates, ambiguous
source preservation, collisions with actual candidate_1 values, immutable inputs,
unknown/legacy selector rejection and forged source rejection. Targeted catalog,
ConversationAgent, parameter-acceptance and observation suites: 147 passed in
5.55s. Independent fresh-context review found no concrete blocking conversion
gap. Search found no remaining current caller using old entity_N selectors apart
from the negative test. Historical captures were not rewritten.

This repairs the model-facing interface, not semantic task completion. No model
replay was launched. Next inspect the actual task19 turn4 planning input and the
whole-goal preparation boundary before registering a fixed replay: preserved
history must be distinguished from the model ignoring it. The shared prompt's
previous failure remains failed evidence, not reset by this interface migration.

Actual turn4 Langfuse inputs inspected at cadc675; evidence report:
`docs/task19-turn4-input-audit-2026-09-08.md`. This changes the attribution: main
planning saw all six previous messages and prices; the denial of available
order/pricing data occurred only in compose after reply verification rejected
the empty structured evidence. Both verification inputs retain history but have
zero facts/outcomes/receipts/pending actions. Current cross-turn context loads
knowledge evidence, not previous business observations; same-turn ResultBoard
retention is not cross-turn support continuity. Independent review requested of
existing durable business-result readers before designing the owner repair.
No new task/model run, prompt edit, annotation score or completion claim.

Business continuity implementation started: source is the governed paired
WorkItem/AgentResult, not generated prose. Private publication metadata captures
typed FactRecord/ReceiptRef observations with original times and source identities
for final, input and approval publications, including reply-verification failure.
This is historical evidence, not a second current business state or approval grant.
Publication fingerprint must include it; empty old records remain empty, without
invented migration. Next connect the existing scoped evidence reader and all model
consumers, with explicit historical validity and no promotion into write authority.

Continuation implementation (in progress, not closure): replaced the knowledge-only
loader port with one ConversationEvidence bundle and one scoped publication read.
Tool-only environments now load it too. Original business observations reach the
shared planning/author/verifier context and domain working context separately from
current verified_facts and approvals. Publication metadata remains private and
fingerprinted; empty legacy publications remain empty. Migrated the loader's
production assembly and scripted consumers; no alternative loader path retained.

Checks before the fresh review: 65 targeted tests passed / 14 skipped; actual PG
business+knowledge continuity tests 22 passed. An earlier combined PG run had 63
passes and one test assertion error (the fake verifier stores context as JSON,
not flattened evidence); corrected the test to inspect the actual contract and
reran. These are no-model checks, not task19 success.

Independent fresh-context review found three remaining causal-boundary issues:
coverage/conflict propagation was lost, receipt/action association lost terms,
and governed write-recovery outcomes without facts were omitted. Capture now
preserves ResultBoard.coverage_for, shares receipt_context with response assembly
(action terms only for matching operation_key), and retains typed write recovery
for matching WRITE operations. Tests added for conflict-dependent vs independent
work, receipt mismatch, and NOT_COMMITTED vs UNCONFIRMED without facts. Publication
interaction protocol updated as well. Those changes require refreshed verification.

Still pending before closure/replay: large historical payload handling through
existing context/archive facilities (not fixed facts pinned without budget),
real application-level mixed wait/failed reply capture and recovery integration,
fresh review of the completed surface. History is not current action authority.
No new model run, encoder enablement, or business-closure claim.

Refreshed checks after the coverage/receipt/recovery projection changes:
101 passed in 20.41s with real PostgreSQL enabled across continuity, publication
through chat cutover, context loader and response assembly suites. Independent
review found no further concrete blocker in those three owner projections;
it requires actual workflow-produced recovery/receipt roundtrips rather than
only constructed dictionaries for final acceptance. The above pending budget
and application-level acceptance work is unchanged. Deliver this as the
historical-observation foundation, not verified closure of task19 or fixed10.

Next verification step at 1bb96fd: actual GovernedWriteRuntime outputs (COMMITTED,
REJECTED, OUTCOME_UNKNOWN with one bounded reconciliation) now go through private
Publication -> PostgreSQL -> recreated TargetTurnContextLoader. The test asserts
the exact original detail/source/effect and receipt action terms, and that loading
history does not call the write/reconciliation ports again. Actual Chat application
publication tests now exercise successful and rejected response verification;
initial test accidentally used template mode, caught by requiring verifier.calls,
then corrected by supplying a scripted composer. 21 PG tests passed before adding
the mixed success/wait extension; that extension is being verified.

Budget diagnosis (no model calls, generated business observation only): one
360,000-character detail string in historical FactRecord makes main planning
return CONTEXT_BUDGET_EXCEEDED before provider invocation; compose requires 90,509
tokens vs 14,200 available; domain pinned prompt requires 90,544 vs 14,200 and
invokes no model. This is not a provider outage. Main planning only trims recent
messages, composition trims none, domain externalizes only verified_facts; the
new historical observations bypass that existing externalization surface.
The archive reader is scoped to domain task ownership, so merely placing its hash
in the main Agent prompt would create an unreadable reference. Resolve a shared
bounded historical-evidence access contract before changing limits or moving
payloads. Source originals stay in the existing private publication record;
do not drop evidence and then treat absence as proof that no earlier lookup ran.

Completed this verification increment: 23 PG tests passed in 21.92s, including
mixed completed read + waiting input and both response-verifier outcomes through
the real Chat publication path. Checkpoint-derived work IDs contain the invocation
prefix; the test executor now identifies its waiting command suffix instead of
assuming an uncompiled command ID. No production branch was added for the test.
The earlier 21-pass run did not cover mixed waiting and is not its evidence.
Next active item remains bounded historical evidence access across all consumers;
this test delivery does not declare the overall repair or fixed10 closed.

Historical original access increment (after ce0b29d): existing private publication
reader now resolves an observation by publication_id + canonical content hash
after tenant/user/conversation SQL scoping. A missing, malformed or cross-scope
reference yields BusinessObservationUnavailable, while database failures remain
database failures. Reads do not invoke a business tool or depend on the recent
window. Original source metadata, coverage, receipt/action binding and write
recovery data round-trip through the typed model. JSON object key order does not
change the identity. No new storage, fallback or domain archive authorization
relaxation. Combined actual-PG continuity suites: 37 passed in 28.14s.

This is the source-resolution boundary, not yet model-facing lazy access. Keep
full history projection until the bounded reader is actually in the capability
catalog and every consumer can distinguish omitted content from absent evidence.
Next implement one generic historical-observation read capability using the
existing native tool/runtime path (including custom benchmark registries), not
a business-specific Skill or an extra planning model. Use structured selection
and bounded output; the installed jsonpointer 3.1.1 can resolve JSON Pointer paths
without a handwritten path parser. Preserve historical scope in returned evidence,
avoid recursively republishing evidence-reader wrappers, and verify budget behavior
at main planning, domain, compose and answer verification before a fixed replay.

Native historical-reader integration (in progress after 29d8e52): the existing
ToolManager/native catalog now exposes one scoped, JSON Pointer-selectable read
for default and custom bundles. The handler uses the installed jsonpointer library,
returns bounded JSON pages, and never refreshes business state or grants approval.
Memory reader wrappers are not recaptured as new business originals. Reassembly
may rebind the reader/principals but must reject a conflicting execution manifest.

Tests exposed a shared owner defect: static Tool schemas used a handwritten subset
validator while dynamic schemas used jsonschema. Static range, nested enum and
additional-property constraints were silently ignored. Both now use the existing
JSON Schema library; integer-valued floats remain legal JSON Schema integers and
are normalized at the pagination indexing boundary. Independent review identified
that conversion bug before delivery; regression covers int and float pagination.

Checks so far: broad suite 178 passed, 223 skipped without PostgreSQL; actual PG
reader/continuity/native-read/security suites 99 passed in 25.18s. These are not
real-model success evidence. Broader PG consumer regression and collision tests
are in progress. No historical budget omission, encoder enablement or tau replay
has occurred. The next causal item remains bounded context across all consumers,
with original access now executable rather than an unreadable reference.

Final checks for this increment: new reader/security suite 43 passed, 1 PG skip;
reader suite with actual PG 14 passed. Broader PG run: 304 passed, 2 skipped,
15 failed because that process had already imported the erroneous property call
`registered_tools()` before its correction. The complete affected set (14 reader
tests plus actual build_target_runtime composition) was rerun in a fresh process:
15 passed in 5.40s. Preserve the failed run; do not report it as an all-green run.
This was a local implementation error in collision checking, not a new business
authority defect. jsonschema-related existing consumers passed the broader run.
Independent review found no concrete new scope/authority escalation; its integer
normalization and same-name replacement concerns are covered by the new tests.
Ready to deliver this increment; overall context-budget and task19 closure remain
unverified. No model calls or business benchmark retries in this increment.

Budget consumer audit at 2915c55: response authoring serializes the identical
conversation context twice, at top-level conversation_context and at
evidence.user_context. The latter is the immutable snapshot used by verification
and revision identity. Remove the redundant top-level copy at ResponseAssembler,
update its schema/consumers and verify author/verifier equality. This eliminates
duplication without dropping evidence, adding a summary model, or changing the
verification snapshot. It does not by itself fit oversized single observations.
Planning and delegated tasks each carry one historical copy; their remaining
large-source projection requires budget-aware selection and readable references.

Single-context delivery checks: 157 passed / 17 PG skips without a database;
the six affected continuity/observation/reply/knowledge-reuse suites with actual
PostgreSQL passed 171 tests in 28.51s. Parameterized short/long history and one
revision verify that source contents occur once in the author request, that the
author evidence equals the verifier snapshot exactly, and that inputs remain
unchanged. No truncation, summarization, or model invocation was used. Historical
frozen benchmark captures retain their old schema; live reply consumers migrated
to evidence.user_context. Retrieval strategies and reranker work remain untouched.

Remaining bounded projection contract: model-call budgets choose whether source
contents can be inline; omitted contents must have executable publication/hash/
pointer references, not a claim that the original was absent. Planning can read
them through the existing native loop. Authoring and verification must share the
same selected evidence snapshot, including selected current read outputs; neither
may independently trim it. Metadata for coverage, original subject/time, receipt
effect and unresolved recovery remains distinct from source content. If mandatory
metadata alone exceeds a call budget, retain a typed budget failure rather than
silently claiming sufficient evidence. This remaining contract is not implemented
or closed by the single-copy change.

Budget projection implementation in progress at 1dcc07f: shared
fit_historical_payload replaces only oversized source bodies (fact JSON, historical
receipt arguments, recovery detail) with executable publication/hash/JSON Pointer
references when the model-call budget requires it. Small bodies remain inline;
source metadata, coverage, receipt effect, recovery state and current facts remain
unchanged. Main and domain planning require the reader in their capability set.
Domain call overhead is deducted before fitting historical bodies.

Independent review rejected an attempted response-stage use: compose/verifier
cannot execute reads, so externalizing there can remove the very support needed
for the final claim. That wiring was removed before delivery; response preserves
the exact full snapshot and existing typed budget failure. This reveals the
remaining cross-stage handoff explicitly: execution must select visible evidence
before authoring, not leave unreadable references for the verifier. A larger
planning budget must not be mistaken for a smaller reply budget. Revision feedback
also remains subject to its actual provider budget, not silently trimmed.

Initial no-model checks after this correction: 70 passed / 18 PG skips across
budget, planning, framework-agent and business continuity tests. Generated 360k
source bodies fit main/domain inputs, main selects the native read, pointers
resolve exact originals, and a moderate history with 8k tool/system overhead is
externalized at the domain boundary. Real PG regression is running. No business
closure or bounded final-response delivery claim; no tau replay or encoder change.

Actual PostgreSQL regression: 102 passed in 28.97s, including process-exit
subgraph recovery; one existing multiprocessing fork warning retained. Added
receipt-arguments/recovery-detail identity and effect-preservation checks; focused
projection suite now 10 passed. This increment covers executable planning views,
not the unresolved selected-evidence handoff to reply authoring. Reply externalizing
attempt was removed; original author/verifier snapshot remains unmodified.

Selected-view handoff diagnosis at eb8e383: ConversationAgent budgets a copied
historical view but discards that view on returning TurnProposal. Manager keeps
the loaded full history in PreparedTurn.context, so final authoring reintroduces
the oversized original despite successful planning/read selection. Repair at the
planning-result/context boundary: carry the program-produced historical view
(not model-generated labels or business authority) into the existing prepared
context for initial and observation planning. Selected native read bodies remain
ordinary ResultBoard facts. Existing checkpoints then retain the same input view;
no second store or reply-side read loop is required. Verify actual Manager ->
tool -> observation -> reply, plus checkpoint serialization and unchanged originals.

Fresh review identified the deterministic bypass: input/approval/reconciliation
does not call ConversationAgent and reloads full history. Host projection now also
runs in the existing Context Loader, independent of ResolutionKind, using the
minimum configured participating call budget as the historical-source ceiling.
Each actual model call still checks its entire input after adding its own payload;
this ceiling is not an assertion that all later prompts always fit. The program
projection handoff retains any further planning-time selection. No main-model call
was added for deterministic continuation. Branch-matrix and real checkpoint/submit
tests are required before claiming this surface closed.

Handoff implementation checks: 136 planning/observation/runtime tests passed,
1 PG skip. Real PostgreSQL run (including reopened checkpoint, source read,
Manager observation, final evidence equality and actual runtime composition):
92 passed in 8.56s. Host projection covers every ResolutionKind without invoking
a model/tool. Additional approval integration with a scripted committed receipt:
1 passed; verifies pending approval consumption, bounded reply evidence and one
execution across replay. This is not an external business API result. Fresh
independent review found no further concrete blocker in the selected-view handoff;
mandatory full-call budget failures remain explicitly supported, not hidden.

Next fixed replay registration: task19 from the original ten-task development
set, offset14/count1, max_steps80, seed300, same configured Pro/Flash roles,
encoder disabled, one run only. Hypothesis: repeated lookup/denial caused by lost
history support and view handoff is removed; the unchanged full customer goal
must retain compatible/alternative action constraints and exact confirmation.
Measure original tool calls, writes/receipts, repeated confirmations, task/user
termination, official ENV/ACTION and judge availability separately, plus full
reply support and state. Passing requires actual requested business state AND
supported customer response; missing judge credentials are not score zero. Keep
any failure and diagnose it before another run. No new training/retrieval tuning.

## Fixed history-handoff replay result (d338acf; not closed)

Evidence: `artifacts/eval/tau3-task19-history-handoff-replay-2026-09-08/`.
One registered task19 replay finished normally (`user_stop`). Official ENV=1
(`db_match=true`) and ACTION=1 (all seven reference actions matched). ALL is null:
the NL assertion evaluator lacks OpenAI credentials. This is not ENV/ACTION=0.
The reference task deliberately prefers the larger saving when both operations
cannot coexist: return $54.04 versus exchange savings $41.64. The successful
return therefore matches its reference database; it does not validate the false
promise that both operations can be completed.

Ten actual tool calls: email lookup 1, name/ZIP lookup 1, user read 1, order read 2,
product read 2, return 1, exchange 1, human transfer 1. Return committed; subsequent
exchange was rejected because the return changed the order from delivered to
return requested. Reply eventually explained this accurately and transferred,
but earlier promised compatibility and repeated confirmation. Native lookups and
retained prices now work; whole-goal feasibility remains unverified/failed.

First-turn failure is independently localized by `target_trace[0:2]`: the author
produced an identity question and verification passed, then publication raised
`PublicationConflictError: publication work control is stale`. It is neither a
missing model response nor a swallowed provider timeout. The publication consumer
currently binds controls from every retained board outcome; the state owner accepts
only the current active revision. Inspect observation revision ownership and
retained-result provenance before changing either contract. A stale result must
not regain authorization merely because its evidence is retained, and a valid
current clarification must remain publishable. The exact revision transition is
not established by the public trace alone; no production bypass is justified yet.

Next: reproduce revision/retained-result publication across the actual observation
loop, define evidence provenance versus publication authorization at their owners,
and independently review that boundary. Separately inspect coupled-action policy
consumption before any new live replay. Encoder remains disabled. No rerun or
business-completion claim accompanies this evidence update.

Actual trace narrows the first-turn trigger (read-only Langfuse inspection):
generation `8ca3b89a8548e33c` invokes email lookup; next generation
`9f9cfd665adf58ed` invokes `cancel_active_work` with empty native arguments, which
the existing adapter binds to the active query. This is not an external concurrent
user revision. Cancellation is accepted, then a clarification passes verification,
but its retained lookup outcome contributes an ACTIVE-only publication binding.
The application confuses evidence provenance with permission to keep executing.

`test_observation_cancel_retains_evidence_without_active_execution_authority`
reproduces this through real Manager prepare/execute/progress/followup/commit and
observation cancellation, then the actual SQL publication guard with a state-backed
reader. One read, preserved facts, cancelled authority, publication conflict. This
is a diagnostic witness asserting the existing failure, not a repair acceptance
test. Initial harness missed the normal followup step; corrected to mirror the
runtime order, then 1 passed. Independent fresh-context boundary review requested
before selecting a publication contract. Do not simply drop inactive bindings:
that would remove protection against genuinely obsolete replies.

Independent review agrees with an existing-aggregate snapshot contract: every
automated Final/Interaction publication binds the exact committed ConversationState
fingerprint used to assemble it, checked under the existing conversation row lock.
Execution guards remain revision+ACTIVE; evidence keeps historical bindings.
Interaction publication must additionally match its current pending signal(s).
Already committed identity/fingerprint replay precedes freshness checks. No new
store, second execution path or model validation call. Tradeoff: unrelated state
changes invalidate an uncommitted candidate; finer concurrent dependency semantics
are explicitly outside this repair. Migrate producers, adapters, command hash,
transaction reader, tests and documentation. Test cancellation/revision and races,
pure replies, all interaction kinds, replay and no partial outbound writes.

Snapshot migration implemented (pending-signal membership remains next): required
`expected_state_fingerprint` replaces historical `expected_work_controls` across
Final/Interaction commands, Target final/fields/approval, failure notice, handoff
selector and delivery metadata. Publication and StateStore CAS share the same
transactional aggregate reader and row lock. Response state is the already
committed `managed.state_after`, not a later reread that could launder a stale
candidate. The static run failure notice loads state when constructing that notice.
Execution guards and evidence provenance are unchanged.

Verification: 101 passed / 1 skipped / 1 failure initially exposed the failure-
notice caller missing the new required argument; migrated that caller. Real PG
suite: 77 passed / 1 failure exposed a direct adapter test missing the argument;
migrated its fixture. New PG matrix initially used the wrong CAS method name
(4 harness failures); corrected to the existing compare_and_set API. Fresh PG
snapshot + delivery + cutover run: 38 passed in 22.24s, covering cancellation,
revision, unrelated state change, pure reply freshness, zero outbound residue on
rejection and immutable replay after subsequent state changes. Observation/control
suite: 54 passed. Fresh independent review found no further concrete snapshot
implementation blocker; signal membership remains explicitly unimplemented.

Migration: command fingerprints intentionally change. Existing committed messages
are recovered through existing-publication lookup; rebuilding an old command with
the new contract is not an identical replay. No old-command fallback is introduced.
Do not declare the publication boundary closed until pending signal membership,
all wait variants and final integrated regressions are verified. No live tau rerun.

Pending signal validation now consumes the same locked state: primary/related
signals must be current and unique, declared interaction kind must match the
selected waits, and suspended/origin controls still require current execution
authority. Already committed replay remains before these checks. Test fixtures
now create real independent pending approval/input with controls and checkpoint
binding rather than inventing standalone publication signals. Initial fixture
migration failed independence validation (8 failures, then 21 failures in a run
that had already loaded the incomplete fixture); checkpoint-bound fixture fixed.
Fresh PG run: 61 passed in 76.79s, including actual approval conversation and
observation/write runtime integration, snapshot and pending-signal matrices,
publication transactions and query projections. No model calls or external writes.
Independent review found no new blocker and requested direct wrong-kind and
inactive-control witnesses; those seven additional PG checks are running.

Additional seven PG checks passed in 10.23s. Snapshot/signal implementation and
bounded database tests are complete; real-model task closure is not established.
Next active item is the already observed coupled return/exchange feasibility
failure, including policy visibility, pre-write promises and confirmation scope.
Do not rerun the fixed task until that causal review determines the required
repair. Encoder remains rejected/disabled; original ten-task evidence is unchanged.

Coupled-action causal review: the actual main, domain and domain-review inputs
contained the whole objective and policy. Their compatibility acceptance was a
semantic false positive, not missing history. Reply verification separately lacked
that policy. A deterministic lifecycle defect also exists: preparation permits
further reads but blocks a subsequent reviewed input/blocker handback, while the
adapter selects the historical pending artifact before the accepted outcome.
The existing accepted handback must own the current proposal selection. A reviewed
NEEDS_USER_INPUT/BLOCKED handback may withdraw an unsubmitted segment proposal;
its evidence remains historical, with no business write or receipt reversal.
Keep one proposal per segment and the existing bounded review; add no task-specific
branch or additional reviewer. Verify both terminal kinds after preparation and
new evidence on the real SDK graph, including retained prior receipts. Policy
transport and semantic compatibility remain separate open work; no live replay.

Withdrawal regression first failed twice: the runtime returned WAITING_APPROVAL
after blocking the new handback. Owner repair allows reviewed interaction tools
after preparation and selects their executed handback over historical proposals.
Independent review found shared review-budget ownership and failure-priority gaps:
successful preparation now closes its decision budget; a subsequent handback has
its own bounded correction, without raising the global model/tool budget. Accepted
handbacks precede ordinary tool failures, not invalid-authority failures. The
16-case matrix covers both handbacks, preparation/handback correction and retained
independent receipts. SDK suite passed 96 / skipped 6. A continuation test initially
reused the exact old execution contract, invalidly replacing its pinned message;
the fixture now uses a new continuation contract as the real manager does.

Continuation fixture additionally needed the same goal control identity on both
revisions (otherwise archive isolation correctly rejected cross-task access).
Fresh final SDK run: 98 passed / 6 skipped in 11.57s, including actual archived
working-message restoration into a new revision, no renewed pending proposal,
and bounded rejection of two post-preparation handbacks. Independent review of
budget and result-priority changes found no new concrete issue. This is bounded
runtime evidence with scripted semantic judgments, not model-quality closure or
a live tau rerun. Policy-to-response transport remains the next implementation
item; do not claim the coupled-action failure fully repaired.

Active policy transport repair: ResponseAssembler receives the existing immutable
capability Registry from runtime composition. Its evidence snapshot includes the
same agent policy descriptions available to planning, bound to Registry version
and fingerprint, separately from conversation history and observed business facts.
Authoring, verification and the single wording repair consume that same snapshot.
No extra policy retrieval/model call, no inferred task-specific precondition table.
Tests must cover direct replies without a board, domain outcomes, wording repair,
and evidence identity changing with policy version/content. This proves transport,
not that a model reliably infers interacting state transitions from prose.

Policy transport implemented in ResponseAssembler v11; runtime composition injects
the same augmented Registry used by execution/planning, not a reconstructed default.
Five tests cover direct/domain replies, the existing one-repair path, unchanged
model-call counts, exact author/verifier snapshot equality and policy-content hash.
Independent review identified completed-unpublished checkpoint bypass, so TurnRuntime
is now v12-policy-evidence and rejects old v11 checkpoints via the existing explicit
migration error before returning a saved reply. No automatic business replay.
Already published messages remain on the existing immutable publication read path.
Initial PG assertion compared against the pre-augmentation default registry and
failed; corrected to object identity with the actual runtime Registry. Verification:
103 passed / 5 skipped; PG composition + TurnRuntime: 17 passed in 4.66s. No live
model/task calls. Full-policy model feasibility judgment remains unverified; policy
presence alone does not close that semantic false-positive defect.
