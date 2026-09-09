# Task 22: operation names and reply lifecycle

Status: operation naming repair verified; reply lifecycle projection implemented;
confirmation-quality work remains OPEN after fresh task validation.

## Evidence and causal model

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

Delivery: scope changes ready for isolated commit/push; unrelated dirty archive and
RAG work excluded. Full task closure and confirmation-quality claims remain blocked.
