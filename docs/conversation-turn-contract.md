# Conversation turns: response or work

The Conversation Agent is the single semantic entry point after state-bound
resolution and an optional compatible Encoder fast path. A turn is not required
to create business work.

| Planning result | Payload | Effect |
|---|---|---|
| `respond` | `response`: customer-facing text | Verify and publish; no work or state transition |
| `resolved` | goals, pending `input_values`, and/or an approval decision | Bind signals, validate, compile and execute |
| `insufficient_context` | missing fields | Existing clarification path |
| `out_of_scope` | no execution payload | Existing unsupported-capability path |

`resolved` means understood, not business-completed. `respond` cannot include
goals, input values, approval decisions or missing fields. A conversational reply
does not complete/cancel an active goal, approve an action or mark an earlier
unpublished question as shown.

## One reply boundary

Planning may already supply the complete response candidate. ResponseAssembler
uses the same evidence verification and Publication boundary as other replies,
without an unconditional second compose call. A rejected candidate may use the
existing one-attempt revision; it never executes business tools. Conversation
history is context, not proof of a newly executed operation. No-business replies
still report `task_completed=false`; an existing wait remains `execution=WAITING`.

## User input is not inferred by string coercion

Client-supplied structured values use the existing deterministic input binding.
Ordinary prose, including a reply correlated to an interaction, first goes to
semantic understanding. The model may propose `input_values` for the current
requested fields; ConversationManager passes them through the existing resolver
and state transition before compiling the resumed DIRECT/DELEGATED work.
Independent goals and independent approval decisions may coexist. Input
submission already resumes its targets, so it cannot also revise or cancel them.
WORKFLOW/ACTION continuations retain their approval/resume contract; unsupported
field continuation is a typed planning rejection, not generic runtime failure.

## Persistence and evaluation

TurnRuntime v8 includes response text in the checkpointed TurnPlan and its
identity. Reopening a completed current-version turn reuses its verified text;
Publication replays the committed response. Older unpublished runtime checkpoints
require explicit reconciliation/migration and are not automatically re-executed.
Already published responses retain normal replay.

The τ³ adapter delivers committed Failed/Reconciling notices without rewriting
their recorded outcome into success. Missing publication remains an adapter
error. This does not supply missing judge credentials or change official scores.

Implementation tests and database restart tests are not model accuracy evidence.
The previous two τ³ results remain unchanged until a separately recorded run.

## Partial input and per-task evidence progress

`input_values` accepts a nonempty unique subset of the current interaction's
`(target_work_item_id, field_name)` keys. Both typed client input and model-proposed
values use the same PendingInteractionState binding. Supplied values and their
provenance persist in the existing WorkItem arguments or legacy Workstream slots.
The remaining interaction keeps its identity and increments its version; old
versions cannot be consumed again. Only work without remaining fields or a
transitive dependency on those fields resumes. Unaddressed tasks keep waiting;
completed independent outcomes remain in the original checkpoint. Legacy inputs
without a target cannot fill a field name shared by multiple tasks.

A partial submission may produce no executable work. It still commits input
progress and presents only the remaining bound questions. Input consumption is
reported to resumed domain work even while peer questions remain unanswered, so
its prior input tool call can be rendered as answered. The existing approval
identity, exact arguments and version remain the authorization authority.

The response author and verifier share ResultBoard's per-outcome `coverage`,
`fact_indexes`, `receipt_ids` and `requested_evidence`. `fact_indexes` refers to
that same payload's facts array. Retained and current outcomes remain paired with
their original WorkItems even when local IDs recur; global coverage is only an
aggregate and cannot satisfy a different task's requirement.

Knowledge loop progress counts source evidence identities, including content,
source revision and index manifest. Query wording, hit ordering, diagnostic
changes and recombinations of previously seen evidence do not reset stagnation.
Archived and inline tool results use the same projection. Old single-digest
archive pointers remain readable; their first comparison with the new identity
may count as fresh observation. Existing bounded recovery and step limits remain
in force. Empty retrievals retain their outcome identity and do not establish
that the overall question is unanswerable. This novelty guard does not judge
semantic answer sufficiency or replace task/approval state.

## Conflict impact and partial delivery

ResultBoard v3 derives conflict impact from the existing structured fact identity
`(subject_ref, requirement_id)` and propagates the detected keys over hard task
dependencies. A late conflict also affects conclusions of already executed
downstream tasks; original execution statuses and facts remain unchanged.
`coverage.conflict_keys` includes both direct and inherited conflicts.
`coverage.deliverable` requires a successful/partial result, full declared
requirement coverage, some factual/receipt support and no conflict impact.
`coverage.delivery_reason` explains conflict, missing requirements, delivery, or
absence of a deliverable result. This is intentionally conservative per task;
it does not partition individual sentences inside an affected task.

`partial_delivery_allowed` is derived from at least one deliverable task and at
least one task that is not fully completed. An unrelated conflict cannot veto
that independent result; a shared conflicting fact affects all of its consumers.
Current dependency IDs resolve within the current WorkPlan. Retained dependency
IDs resolve within retained outcomes; ambiguous historical IDs conservatively
combine candidate conflict impacts rather than joining to a new current task.

ConversationAgent.compose() receives original evidence plus this derived
coverage, and the final verifier receives the same context. Both treat affected
conclusions as unestablished while retaining independently supported answers.
Template rendering and knowledge-failure fallback use the same original board;
they suppress affected factual conclusions and explain the limitation. Committed
action receipts remain reportable execution facts. No approval or execution
record is retroactively rewritten by a publication conflict.

This contract covers detected structured fact conflicts and represented task
edges. It is not a new semantic contradiction detector for arbitrary document
passages, and it cannot recover a dependency absent from both the graph and the
fact associations. Model compliance remains subject to answer verification.
