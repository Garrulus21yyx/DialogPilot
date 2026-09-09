# Approval scope and delivery convergence

Status: IN_PROGRESS; no business closure asserted. Baseline HEAD 89feac2.

## Evidence and causal model

Task22's domain worker understood both address changes, prepared the account
change and supplied the order change in `operation_plan.remaining_steps`.
The preparation adapter validates then discards that plan. The conversation wait
owns one concrete action; the response sees a broad objective and one proposal,
not the proposed sequencing. First author solicits both; revision drops the order.
The judge also falsely reports absent NY/USA, then twice returns empty arguments.
On response-only turns retained approval exists, but error delivery reads only an
empty current ResultBoard, producing “No result is available yet”.

These are distinct execution-scope, projection and verification failures, not a
failure to recognize the original two goals. Invalid judge output is not a negative
business result. Its model-side cause is not established.

## Positive contract and owners

- Preparation owns validated concrete operations; a future plan is not a grant.
- Approval binds the exact presented operation set. One decision may cover several
  prepared actions, each retaining its operation identity and receipt. Unknown
  future parameters cannot inherit approval.
- Task progress distinguishes prepared actions, unfinished work and committed
  outcomes. Authors, verification and failure delivery consume the same view.
- Reply failure leaves pending work visible and actionable; it does not manufacture
  missing business results or authorize writes.
- Independent writes need not be atomic. Failed/changed prerequisites affect only
  the corresponding operation and dependent work.

## Sequence

1. [done] Inspect complete preparation/approval/resume/persistence/publication
   surface; independent audit. Preserve unrelated user edits.
2. [pending] Implement one coherent operation-scope and progress contract, migrate
   producers/consumers together. Do not infer prepared parameters from plan prose.
3. [pending] Remove redundant semantic approval gating where runtime data owns the
   decision; retain targeted factual verification and typed model errors.
4. [pending] Verify invariants across multi-action decisions, partial results,
   reply-only failure and persistence/resume. No paid/business rerun at this stage.
5. [pending] Independent review, record remaining uncertainty; commit/push owned
   changes only. Business success requires separately authorized live validation.

Non-goals: new runtime, broad prompt rewrite, case22-specific address logic,
automatic approval of future actions, extra online judges, historical replay edits.

## Delivered stage: durable interaction delivery (not batch approval closure)

The review separated a bounded recovery repair from the larger approval-scope
migration. Existing state already preserved the pending approval. The failure was
presentation selection: response-only mode overrode the publication record, and
failure text only read the current execution board.

- `approval_presentation_due` selects an undelivered original signal independent
  of planner mode. A successfully presented retained signal is not re-solicited.
- TurnRuntime re-authors an undelivered approval/input from persisted bindings,
  including compound waits; it does not dispatch another domain/prepare call.
- Publication uses the corresponding approval/input bindings for the actual
  presented interaction, even on a conversational turn.
- Error delivery preserves current safe outcomes and durable waits, including
  knowledge verification failures. No raw parameters, model drafts or grants are
  emitted by this status rendering.
- Turn runtime v21 prevents an old unpublished v20 assembled checkpoint from
  bypassing the new owner rule. Existing version rejection remains explicit;
  published transcript replay and persisted business approvals are unchanged.

Checks: 203 passed in 52.09 seconds with local PostgreSQL enabled. Includes
PostgreSQL state-store reload after a rejected presentation, unchanged signal and
arguments, no repeated preparation, and no repeated interaction after successful
presentation. These are deterministic fixtures, not live business/model scores.
Independent review first found two missed branches (knowledge-safe rendering and
compound input publication); both migrated and covered before final verification.

Earlier broader check: 197 passed, one failed in the existing six-scenario HTTP
fixture. Its scripted planner still emits `execute_refund`, removed by d8e8933;
failure occurs in unchanged ConversationAgent goal validation before this repair's
reply path. That fixture has NOT been rewritten or claimed green. An intermediate
new PostgreSQL test run also exposed test identity reuse across parametrizations;
isolated conversation IDs fixed the fixture, without production special cases.

## Open scope and next work

Batch approval is NOT implemented by this stage. PendingApprovalState, grants,
continuation commands and publication attestation still bind one operation. The
domain plan is still not a prepared group. The final semantic judge's false
NY/USA finding and occasional empty output are not fixed by delivery recovery.

The next migration must represent one immutable prepared operation set at the
preparation owner (a single action is a one-member set), carrying each operation's
exact parameters, original operation key, prerequisites and order. Approval maps
one explicit whole-set decision to those members; execution preserves per-member
receipts/partial outcomes. Unprepared future work remains outside the grant.
Affected boundaries: AgentResult, PendingApprovalState/AcceptedApprovalState and
PostgreSQL codec, deterministic resolution/default commands, RoutePolicy,
WorkPlan continuation, response evidence and Publication attestation. No second
single-action runtime or prose-to-grant conversion is an acceptable migration.

Reference checked 2026-09-09:
https://docs.langchain.com/oss/python/langchain/human-in-the-loop
Official HITL represents simultaneous requests as a batch with one decision per
action in request order; interruption/resume is framework-owned. Applying one
user approval to the explicitly displayed batch is an application mapping, not
blanket permission for later model choices. Newer documented conditional hooks
require >=1.3.3; do not assume them available in this repository's pinned SDK.

Full work remains IN_PROGRESS. No new tau run, real business write, or claim of
task22 success was made during this stage.
