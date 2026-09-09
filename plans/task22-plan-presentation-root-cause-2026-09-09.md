# Task 22: operation names and reply lifecycle

Status: operation naming repair verified; reply lifecycle projection implemented;
confirmation-quality work remains OPEN after fresh task validation.

## Active continuation after 09fcb8b

User requests completing the confirmation repair. Reconcile: source HEAD 09fcb8b;
unrelated dirty archive/RAG work remains excluded. Scope: author/presentation,
existing assessment contract, its publication/revision/persistence consumers.

Hypothesis: approval scope is conflated with answer coverage. Explicitly assess
whether a reply requests permission outside the selected proposal, in the same
existing model call. This is an experimental contract change, not proven by naming
a new field. No extra online judge, business-specific branch, or keyword filter.

Experiment: download the two full failed verification snapshots from Langfuse;
retain their exact source inputs. Compare original vs one additional independent
approval_scope_valid judgment across both failures and positive counterparts
(plain missing-value question, exact selected-action approval, retained approval
discussion, ordinary conversation). Same verifier/model/token budget; at most
16 calls, no business tools. Adopt only if failures are caught and valid replies
remain allowed. All results retained. Then migrate every assessment consumer and
test combinations, review fresh-context, and validate the fixed user interaction.

Calibration result: new approval_scope_valid returns true for both full-context
failures, just like the original assessment. Rejected; no production schema change.
Next hypothesis is input authority salience, not another output check: derive a
compact selected-proposal presentation view (exact action description and arguments,
whether this reply has any selected approval) from the same evidence. Put it at
the final model-message boundary for author and verifier; retain complete evidence
for grounding. No new state authority or policy extraction. Test exactly the two
failed full snapshots with original output schema first (2 calls), then positive
controls and author output only if the failures are distinguished. This expands
the diagnostic budget explicitly; it does not retune on held-out data.

## Evidence and causal model

Continuation experiment result: the appended presentation view also passed both
invalid replies. Not adopted. These are full *logged* snapshots, with the existing
Langfuse address redaction preserved, not unredacted production replays.

Next bounded diagnostic: two original-schema/original-input calls with the existing
DeepSeek verifier profile set to HIGH reasoning (4096 completion floor, 8192 cap).
No new judge, no production configuration change until measured. This separates
reasoning configuration from input projection; original snapshots and failure labels
stay fixed. Official API compatibility checked 2026-09-09:
https://api-docs.deepseek.com/guides/anthropic_api/ .

Independent review found a concrete projection defect: worker-segment
WAITING_APPROVAL and accepted internal PREPARE_ACTION review are adjacent to the
entire two-action objective; an identity-free duplicate is passed as agent_outcomes.
Test a lossless authority-separated view on the same two failed snapshots (2 calls):
retain goals/history/facts, distinguish requested objective from observed segment,
remove accepted internal-review diagnostics and duplicate outer fields. No change
to the approval owner or selected proposals. Adopt only on a positive author/judge
comparison, not solely on the cleaner source structure.

Results: authority-separated view catches the over-broad two-action approval but
still accepts the unprepared confirmation. HIGH reasoning with unchanged original
inputs/schema catches both, with explicit scope feedback (also flags redacted
addresses; do not count those incidental flags as success). Next 4 calls: generate
replies for both snapshots through the existing composer with HIGH reasoning and
judge two valid controls (missing state; selected account-only approval). Adopt
role configuration only if generation improves and valid questions remain allowed.

Positive controls passed. Both HIGH composer outputs distinguish the selected
account-only approval from the queued order update; the information question no
longer requests execution approval. However that question included an internal
observation hash citation, so it is not declared a fully valid customer reply.
Next: evaluate both generated candidates through the existing production verifier
and its one-revision author path (max 6 calls; no business tool replay). This checks
successful delivery, not just detection. Record model latency/token cost separately.

HIGH rejected for delivery: one generated reply verification exhausted all 8192
output tokens in thinking and produced no tool result. Defaults/local settings
restored to NONE/0, no deployment/budget widening adopted. Full structural review
also identified the shared domain-review role and history-budget coupling.

Next hypothesis (2 calls then controls only on success): the existing long judge
prompt mixes many workflow corrections into semantic support assessment. Replace
it with a concise role/authority/output contract, same original schema and NONE
model, full logged snapshots. This is a replacement, not additional instructions,
new fields or another judge. Scope: evidence support, request coverage and exact
selected approval terms. Do not adopt without positive/negative boundary controls.

Concise instruction result: prepared-scope case rejected, unprepared confirmation
still passed; do not adopt this as a complete repair. The latter output places
verdicts before a contradictory issue. Final bounded diagnostic (2 calls): same
schema fields and concise instruction, serialize issues before verdicts and ask
for defects first. Tests whether early categorical output is masking examination,
without adding a reasoning field, model call, or post-hoc substring gate.

Defects-first test detects both intended scope violations, but also emits incidental
incorrect feedback (it misreads the state question as asking apartment details).
Therefore test 4 positive controls from the same logged contexts before adoption:
plain state question, exact account-only approval, honest not-executed explanation,
ordinary clarification. Same output fields/model; at most 4 calls. The known
failures remain development cases, not held-out performance claims.

Final diagnostic result: defects-first rejects all four valid controls, including
the correctly scoped account-only approval. Rejected. No concise/defects-first
judge prompt, output-schema extension, or reasoning configuration is adopted.

Implemented bounded structural repair:
- Separate requested objective from observed worker segment for author/reviewer.
- Pending action projection contains only exact action identity/arguments; the
  parent goal is not duplicated inside that proposal, including the worker-result
  (non-persisted approval) branch.
- Remove accepted internal-review diagnostics and duplicate identity-free outcomes
  from reply evidence; original execution trace remains intact.
- Populate retained proposal details before the conversational early-return path,
  without re-presenting, consuming, or modifying that approval.
- Author/revision/verifier continue sharing one evidence snapshot and binding.

Verification: 386 checks passed with real PostgreSQL enabled before the final
pending-objective removal; rerun the affected contracts after that removal.
One pre-existing test expected a no-freshness-lease order read to be reported as
current. Reproduced using HEAD response_assembly loaded in memory, then corrected
the test oracle to assert the dated historical observation (no production change).
Independent review covered all projection consumers, runtime early return, model
configuration and checkpoint behavior. Rejected HIGH was reverted everywhere it
was tried; Compose and role defaults remain unchanged, as do context limits.

Status: structural repair implemented; confirmation semantics NOT CLOSED. Logged
snapshots are redacted, all failed model experiments retained. No new business
task was run this continuation; no claim of address updates or task-22 success.
Next investigation must address authoring from authoritative interaction state,
not add another online judge, grow this prompt, or retry the same business writes.

Final checks after both proposal producers were aligned: 317 passed with PostgreSQL;
the broader 386-check run also covered framework transport and deployment-facing
contracts. Independent fresh-context review found no new deterministic blocker in
the adopted diff. Model diagnostics: 32 calls total, all outputs preserved under
`artifacts/eval/confirmation-scope-full-context-2026-09-09`; no task writes executed.
Delivery: scoped commit/push follows, excluding unrelated archive/RAG working tree.

Baseline HEAD 1408147. Original run: `artifacts/eval/tau3-task22-role-policy-2026-09-09`.
Langfuse session `tau3-7fe61180eb90477180da9ee851f13cff`:

- Observations `69dae32c6e310f2e`, `d6ce092e37f89e48` call
  `prepare_modify_user_address` with remaining step tool `modify_pending_order_address`.
  The validator accepts `prepare_modify_pending_order_address`. Both preparation
  capabilities were available. The public schema leaves this field unconstrained.
- Generic capability rejection made the actor remove the other requested goal
  (`4be308e284133b70`, `0aab35bcb89a033c`). Domain review then rejected goal omission.
  This is a model-facing naming contract defect, not missing business permission.
- Reply judges `2c028c95ccc89f34` and `e38ee889b7e4ea94` disagreed on nearly identical
  unbound confirmation questions over unchanged evidence. Existing semantic review
  is not a deterministic authorization proof.
- Terminal failure replies promised continued work. Assembly knows execution has
  ended, but exposes task incompleteness without an explicit continuation fact.

## Bounded target contract / owners

1. Tool assembly publishes the exact task-scoped preparation-tool enum. Structural
   validation uses the same schema. Invalid names yield actionable, non-sensitive
   feedback; no guessed aliases, broadened permissions, or lost goals.
2. Conversation planning chooses action calls or a reply. Returning text does not
   schedule work. Runtime assembly occurs after execution/observation is finished;
   persistent waits allow later continuation but do not mean background execution.
3. Prepared approval remains owned by existing conversation state. Author and
   existing verifier consume the same selected/retained proposal and lifecycle
   evidence. Ordinary missing-value questions remain valid.

## Scope and non-goals

Change existing schema/adapter, reply state projection and shared instructions.
No new engine, judge call, text classifier, retry limit increase, or address-specific
branch. Preserve unrelated archive/RAG edits. No business rerun until source checks.

## Validation / exit criteria

- Generated tool scopes: schema, model-visible tools, runtime validation agree.
- A wrong name can be corrected without removing remaining goals; original graph
  and metadata remain durable but are not business authorization.
- Reply snapshot survives composition/revision with identical lifecycle evidence;
  unfinished, failed, waiting and completed outcomes never imply scheduled work.
- Existing question, approval, partial-result, recovery and framework tests pass.
- Independent review plus bounded model probes; semantic quality claims require
  model evidence, not scripted judges. Full task score reported separately.

## Bounded model probe preregistration

After source repair, run 2 existing feasible operation fixtures (`lock_and_rename`,
`publish_after_label`) through `evaluate_action_boundary.py`, first-decision only:
4 model calls total, original model policy, 4096 output tokens, no business tools.
Check exact callable names and preservation of the remaining dependent goal.

Run 4 presentation fixtures through production composer/verifier (8 calls):
terminal failure, retryable failure, ordinary missing choice, prepared approval.
The first two must explain stopping without autonomous-continuation promises;
the latter two must remain useful questions. No extra retries or prompt tuning
after seeing results. These are component probes, not task 22 or held-out scores.

Probe correction (before additional calls): the existing script's reviewer tests
its fixed candidate, not the actor-produced plan. Both fixed candidates omit the
new operation graph and were rejected; one review also made an incorrect ordering
claim. Preserve these failures. Add exactly 2 reviews of the captured actor plans
(no actor retries), and 2 checks of old invalid reply meanings over the explicit
terminal/no-prepared-action snapshot. No further production/prompt edits from these
probe results. Maximum model calls now 16, no business execution.

Progress: implementation complete; 218 initial checks passed / 1 skipped. Broader
checks exposed older prompt-refactoring test assertions requiring business policy
inside tool descriptions. HEAD already projects that policy via operation references;
tests now verify preservation there plus the new enum, rather than restore duplication.
Independent review found the probe caller and planner-prompt expectation needed the
same migration; both updated. Final scope regression: 371 passed, one multiprocessing
fork warning, including real PostgreSQL subgraph restart, write recovery and HTTP.
Both actor-produced plans use exact callable names, preserve their remaining goal
and pass the existing domain reviewer. All four generated replies are publishable;
both old invalid replies are rejected. This is not statistical assurance against
all semantic errors, and fixed-candidate review failures remain recorded.

## Single task validation preregistration

After the above source checks, repeat only original task 22 (offset 17, count 1),
same actor/reviewer policy and simulator limit 80 / user max tokens 512, no retries.
Output: `artifacts/eval/tau3-task22-operation-contract-2026-09-09`.
Criteria: both requested address writes, no premature/unbound approval question,
truthful public outcome, official environment/action/overall scores separately.
Retain any failure; do not change production or add special cases mid-run.

## Fresh task result (not closed)

Task 22 completed with user_stop; ENV=0, ACTION=0, ALL=0, no evaluator errors.
The former operation-name failure is absent: a real proposal was successfully
prepared at turn 6 (approval signal recorded), with no DomainOutcomeRejected.
No writes occurred: the user withdrew the requested changes at the approval turn.
The system must honor that withdrawal, not execute to improve the benchmark score.

Confirmation presentation still failed:
- Turn 5 combined the missing state question with permission to perform both changes.
- Turn 6 described both requested changes as prepared although only one proposal
  was selected. The existing verifier accepted both replies.

Langfuse session `tau3-2928d699366646048d853e01e0d1d686` confirms production inputs:
`1e8b97a99a9663d7` received no-selected-action instructions and returned supported /
answered true. `a90bb444866d2a1c` received the selected-proposal scope restriction and
returned all three checks true. `4da62aef986fa0f1` produced preparation and
`dd7bff68bb7b4a8b` accepted the domain plan. This is a semantic false negative over
an already expressible contract, not evidence that another boolean or classifier
would automatically solve it. Independent review reached the same conclusion.

No production branches or prompts were added after this failed validation. A
possible separate approval-scope assessment dimension must be tested against the
original full snapshots plus valid choice/retained-approval controls before adoption;
it is not an established repair and is not implemented here. Existing deterministic
approval binding remains authoritative; natural-language review cannot grant writes.

Delivery: implementation, tests and all probe/task evidence committed and pushed as
`ad2e4d0`. An isolated Git index excluded unrelated dirty archive/RAG edits, including
archive-only hunks in the shared framework-agent file. This status attestation is
delivered separately. Full task closure and confirmation-quality claims remain blocked.
