# WorkPlan single-contract repair

Base: d604072. User explicitly requests removing compatibility paths, not adding
fallbacks or migrating old execution records. Unrelated dirty work remains owned
by the user and is excluded.

## Observed cause and target contract

Review reproduced three failures: an empty inferred approval scope becomes
COMPLETED; a consumed wait permits a self-supplied expanded resume envelope; a
foreign-plan result loses its scoped identity before Board consumption. Mixing
legacy and scoped replay also produces duplicate local results. Shared cause:
new contracts are bypassed by compatibility consumers at authority boundaries.

Target:
- Graph result updates are exclusively PlanScopedAgentResult. Producers bind
  plan/item identity, reducer accepts identical replay and rejects conflicting
  identity, and every reader verifies the binding against the active WorkPlan
  before projecting AgentResult. Raw worker returns exist only inside the worker
  execution boundary, not as an alternative State schema.
- Checkpoints must contain the current plan fingerprint and current contract;
  no legacy fingerprint, implicit plan-policy reconstruction or raw-result path.
  Old records are not deleted, executed, or silently upgraded.
- Resume requires a previously accepted envelope from persisted waits/grants or
  resolver output. Consumption carries that envelope forward; active goal metadata
  cannot replace it. A fresh goal revision is not a legacy resume.
- An approval decision binds a recorded grant and its nonempty operation scope.
  Manager consumes the bound outcomes; absent evidence is not completion. No
  inferred singleton approvals or operation-key-only fallback.
- Recovery observations consume the typed producer contract, without filling
  missing legacy fields at the projection boundary.

## Steps

1. done — remove compatibility at result, checkpoint, resume and approval boundaries.
2. done — migrate affected callers and fixtures to typed scoped results, accepted
   envelopes, explicit approval operations and complete recovery observations.
3. done — local contract matrices and PostgreSQL integration; scoped diff review.

No paid model/τ³ run; no RAG changes; no new runtime or business state machine.
Evidence will distinguish implemented contracts, local verification and any
remaining failures. Do not treat existing aggregate test counts as closure proof.

## Implemented ownership

- Worker returns AgentResult internally; runtime binds it exactly once to
  WorkPlan/WorkItem. Reducer accepts only PlanScopedAgentResult. Readers validate
  scope before projecting, including cached-complete execute/resume/cancel paths.
- Serializer rejects persisted WorkPlan without policy. Fresh WorkPlan's typed
  default remains part of the current construction contract, not a decoder repair.
  Dictionary policy coercion and legacy fingerprint acceptance are removed.
- RoutePolicy accepts continuation only from waits/grants or explicitly supplied
  owner-validated envelopes. Consuming a wait does not widen task capabilities.
- ConversationState.accepted_approval supplies the grant to execution and lifecycle
  projection. Manager uses ResultBoard coverage for every approved operation;
  neither an empty inferred set nor bare SUCCEEDED is proof of completion.
- Action decision imports require operation identity. BusinessObservation no
  longer fills omitted recovery fields; the existing write producer supplies them.
- ResultBoard dependency projection requires WorkPlan; no plan-less policy branch.

## Verification, 2026-09-09

Against the working tree based on d604072 (unrelated edits preserved):

- Combined 24-file contract/Manager/runtime/approval/recovery/observation suite:
  **708 passed**, PostgreSQL enabled, 110.51 s.
- Architecture/outcome/domain-boundary plus WorkPlan checks: **609 passed**.
- Final WorkPlan + action-decision checks after checkpoint-import validation and
  cached-complete adversarial cases: **97 passed**. Suites overlap; do not sum.
- Property matrices cover reducer association/permutation/replay, all 25 pairs
  of approval member statuses with receipts, absent grant/outcomes, consumed
  input provenance, task-scope expansion, checkpoint roundtrip and legacy rejection.
- Extended observation/response run: **249 passed, 4 failed**. All four fail
  before contract execution: test_business_observation_continuity.py:135 calls
  _interaction(identity), while test_postgres_publication.py:136 requires pool.
  Both mismatched lines confirmed in `git show d604072:<path>`; unchanged here.
- `git diff --check` passed. No paid model/τ³ run; no full-suite clean claim.

Old records remain intact but unsupported execution shapes are rejected, not
automatically migrated/replayed. No SQLite, alternate executor, fallback engine,
or new business state machine was introduced. Runtime production code shrank.
Delivery: scoped commit/push follows these checks; unrelated work is excluded.
