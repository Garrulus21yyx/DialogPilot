# Conversation capability execution convergence

Status: repair in progress; direct executor identity migration implemented, full
capability exposure and goal-continuation repair not complete or verified closed.
Scope: failures from the fixed ten-task run after the rejected encoder trial.
Baseline evidence: `artifacts/eval/tau3-new10-after-encoder-trial-2026-09-08/`.
Current inspection HEAD: ceeab4e, with existing user-owned business recovery changes.

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
