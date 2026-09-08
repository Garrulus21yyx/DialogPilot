# Fixed10 v3 — running; no completion claim

Frozen runner commit: `e646ee6`, including interaction migration `71db686`.
Live execution handle at launch: exec session8342. Check the actual process before
resuming observation; manifest RUNNING alone is not liveness proof.

Same development task IDs4/6/7/8/10/11/13/14/15/16 and seed300 as v2. Maximum80
conversation steps,20 actor calls per work item,2 outcome reviews per segment;
actor/reviewer completion override4096, simulator512. Actual reviewer input
budget28072. The manifest records model profiles and dirty-worktree source
hashes. Concurrent archive navigation changes prevent treating the run as a
perfectly isolated causal ablation of interaction handling.

## Acceptance, before results

- Keep official ALL, ENV and ACTION separate; missing judge credentials produce
  unavailable scores, not zero and not proxy official grades.
- Inspect each visible approval against the exact prepared operation and its
  parameters. Different operations or revised parameters can legitimately need
  separate decisions; repeating an unchanged authorized operation cannot.
- Compare actual tool receipt/environment with the final per-goal state and
  user-visible claim. Internal completion or a polite ending alone is not proof.
- Check compound replies preserve corrections, field values, decisions and
  independent goals, with appropriate preservation of waiting work.
- Attribute failures to the first proven owner boundary; distinguish model
  interpretation, runtime/contract, simulator and evaluator failures.
- Preserve every fixed task and failed attempt. Do not replace rows with selected
  successful repeats or modify production during this batch.

This is a development regression, not held-out accuracy or production readiness.
Fill the results only from completed task files and trajectories. No task result
was available when this report was created.

## Task4 — business writes pass, repeated confirmation remains

Completed trajectory: ENV1, ACTION0, official ALL unavailable due to missing
judge credentials. Both `modify_pending_order_items` calls match the reference;
ACTION0 is the missing read of product6086499569, not an unsuccessful write.
Final public task_completed=true and all projected outcomes SUCCEEDED agree
with the two successful order modifications.

However, assistant message24 asks permission for **both** order changes together;
user25 explicitly approves both. Assistant28 reports the first done and asks
approval for the second again. The shown scope and the approval binding are not
aligned. This falsifies repeated-confirmation closure even though ENV passes.
Do not describe the second question as a newly disclosed distinct decision
without checking what the first message promised. The likely boundary is
composition/verification of a prepared action versus queued work; prove the
authoritative pending snapshot before assigning root cause. No runtime patch
has been applied during this batch.

Trace caveat: final evaluation_trace.cost.conversation_planner_invoked=false is
derived from the final deterministic resolution kind in the current projector.
The new whole-turn semantic approval is subsequently bound into
APPROVAL_DECISION, so this field alone cannot prove that planning was skipped.
Use actual model-call observations for invocation/cost attribution.

## Task6 — revised goal carries a retired source binding

ERROR at final confirmation, not an official0. The user explicitly removes the
bottle, the application presents the lamp-only proposal, then the user approves.
RoutePolicy._accept_command rejects `command carries a stale or unauthorized
binding` before the exchange is executed. The initial stale-goal behavior is not
the trigger this time: the revised objective correctly says lamp only.

Read-only inspection of the still-live benchmark database established:

- Conversation tau3-9c6f2b4065c14162ba72dcef1587ee8e, state version10.
- New approval operation47272e… contains only lamp8384507844→7453605304;
  its suspended objective is control revision5 and has order_id #W6390527.
- That suspended WorkItem's order binding is WORKSTREAM_SLOT of old operation
  79ca1b… source_version1. Its original workstream is now PROPOSAL_SUPERSEDED,
  CANCELLED, state_version2; order_id itself remains #W6390527.
- New operation47272e… is PREPARED/WAITING_APPROVAL, state_version1.

EntityBinding.valid_for requires equality with the current source workstream
version; unchanged continuation copies the historical binding. Thus a valid
argument accepted during revision becomes stale through the same revision's
retirement of its source. The state transition/parameter provenance boundary
must preserve accepted argument evidence without treating old approval as new
authorization. Do not bypass all stale checks or replace values in a report.
The action's own arguments have no old binding; failure is in the suspended
domain goal's continuation. This evidence was read before runner DB cleanup.

## Independent review — remaining shared verifier contract

The actual AnswerVerifier still derives approval_required from pending_actions
AND NOT requested_inputs, and claim_verification's prompt treats inputs as
exclusive of approval. The earlier real-Assembler test used a scripted verifier
and therefore did not prove this consumer's semantics. Composer/publication now
support compound interaction, but the downstream verifier remains on the old
algebra. Treat field sufficiency and presented action scope/terms as independent
dimensions at these existing owners; do not introduce a second author/verifier
or force model-created fact bindings. Task4 additionally requires the requested
authorization scope to match actual prepared snapshots, not merely contain some
valid terms while including queued goals. No production edits during this run.

## Task7 — successful reduced-scope exchange

ALL1/ENV1/ACTION1, final task_completed=true. User23 withdraws the bottle before
the action is prepared. Lamp-only revision survives; after payment-method choice
and a single presented lamp approval, one exchange commits and assistant38
reports the correct $5.08 charge. This is a successful path, not evidence that
Task6's already-prepared-source revision is solved. Keep the different timing
of scope change in the causal comparison. Existing asynchronous store teardown
warnings remain visible and are not relabeled business failures without evidence.
