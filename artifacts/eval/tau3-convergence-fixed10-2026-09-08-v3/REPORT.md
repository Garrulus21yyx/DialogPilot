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

## Task8 — prior stale-goal witness now succeeds before proposal preparation

ALL1/ENV1/ACTION1, final task_completed=true with committed action and completed
domain outcomes. User25 withdraws the bottle; the lamp-only goal subsequently
obtains payment choice and approval, then one exchange writes the correct lamp
and $26.34 difference. Final response40 agrees with the receipt and explicitly
leaves the bottle unchanged. This differs from Task6: the source being revised
was a field wait, not a prepared action workstream that retirement invalidated.
The earlier broad stale-objective witness is improved, while the prepared-source
transition remains disproved. Assistant24's refusal to help choose a bottle is
a dialogue-quality concern, not proof of unavailable tool capability.

## Task10 — same binding/transition defect in a compound denial

ERROR at turn3, before any return. Assistant first presents exactly orderW5490111
and correctly marks W7387996 as later work. The user says "No, refund that one
to my other payment method." RoutePolicy then raises the same stale-binding
error. Unlike Task6's next-turn continuation, here semantic arguments were
selected against the loaded pre-decision state, then Manager locally consumes
the approval before final validation. A source version can change inside the
same planned transition. The repair must account for both initial selection
validation and retained accepted-argument provenance, not only continuation.
This task also shows that single-proposal wording can be correct; Task4 cannot
be explained merely by all conversations always showing two proposals.

## Task11 — contradictory verification then an invalid planner continuation

ERROR, no completed return. After the user requests reversed refund methods,
the actor prepares the first return using the supported original payment method.
The first candidate explains the policy and asks approval of that first prepared
return. The verifier rejects it with contradictory reasons: it must request
approval, but asking to proceed is redundant; it must also prepare the second
return, although the pending action only covers the first. Revision broadens the
question to both returns and is rejected again. The published fallback is only
"This request: Awaiting approval; the action has not been completed."

The frustrated next user asks why. Planning produces unchanged continuation of
the still-undecided origin; StateBound correctly rejects execution without an
approval decision, but the conversation ends as target_runtime_failed rather
than an explanation. Distinguish the valid execution guard from the poor plan
and prior contradictory verifier prompt. Do not remove authorization checking
to make this task pass. The trace preserves both rejected candidates/verdicts,
the fallback and final error. This is additional evidence of interaction-purpose
and incremental-work acceptance ambiguity, not a refund-specific exception.

## Task13 — corrected target succeeds, an unnecessary scope question remains

ALL1/ENV1/ACTION1, final task_completed=true. Initial message12 again calls the
keyboard non-gaming, but user13 explicitly excludes keyboard/mouse and chooses
the original card if PayPal cannot be used. This correction is preserved; one
return writes exactly4579334072/6117189161/4947717507 and the final reply agrees.
Unlike v2 the wrong fourth item is not returned. Assistant14 still asks the user
to reconfirm the already-explicit target set before actual proposal16; record
this as avoidable dialogue, not a second committed operation. Successful official
reward does not establish that unnecessary confirmation is eliminated.

## Task14 — no-progress domain result and failed explanation

ERROR before any return. User asks for gaming-related items; after identifying
the orders, the system asks for exact items again. On the next turn the domain
review rejects repeated refund-method questioning: only the original PayPal is
available for the selected mouse return, so it requests preparing the action.
The actor does not reach an accepted proposal within its bounded reviews.
Composition subsequently claims it will prepare a return and is rejected because
the outcome is TERMINAL_FAILURE with no proposal. A generic failure is published;
the user's request for explanation then ends in runtime failure. Preserve the
actual feedback/actor handling and final stack for post-batch attribution; do
not turn this into a gaming-item or PayPal-specific rule.
# Terminal batch audit

The original exec session8342 exited with code0; all ten fixed task result files exist. The runner completing is not application success. No task was restarted or substituted.

| Task | Recorded status | ALL | ENV | ACTION |
|---|---|---|---|---|
|4|EVALUATION_INCOMPLETE|unavailable|1|0|
|6|ERROR|unavailable|unavailable|unavailable|
|7|EVALUATED|1|1|1|
|8|EVALUATED|1|1|1|
|10|ERROR|unavailable|unavailable|unavailable|
|11|ERROR|unavailable|unavailable|unavailable|
|13|EVALUATED|1|1|1|
|14|ERROR|unavailable|unavailable|unavailable|
|15|EVALUATED|1|1|1|
|16|ERROR|unavailable|unavailable|unavailable|

Four official ALL passes, one completed simulation with unavailable ALL judge, five application errors. This is not a complete ten-task success rate and not business closure. Task4's missing judge and five execution failures remain different failure classes.

Task15 is a positive single-proposal witness: payment choice at message19, complete size/material/waterproof and price-difference proposal at20, approval21, successful modification and accurate final reply24. ALL/ENV/ACTION are1. It does not test compound approval/revision.

Task16 extends the shared failure evidence: user17 supplies both cancellation targets/reason and the watch refund method; reply18 is the unassembled awaiting-approval fallback. Reply30 finally asks approval of the first prepared cancellation while identifying the second as future work. User31 approves cancellations and asks the watch refund amount. The application then fails in RoutePolicy with `command carries a stale or unauthorized binding`, as in6/10. Thus parameter acceptance ordering and interaction verification are coupled in the same multi-goal conversation, not independent product-specific exceptions. No new benchmark run should precede the owner/consumer migration described in the maintained plan.
