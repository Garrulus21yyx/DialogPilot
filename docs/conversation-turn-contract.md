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
