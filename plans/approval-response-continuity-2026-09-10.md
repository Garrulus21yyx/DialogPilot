# Approval response continuity

Scope: task20 exposed a split between a prepared action, a model-authored prelude,
and a mechanically rendered argument card. Internal provenance was also exposed
as public citations. This item changes presentation, not execution or read reuse.

## Contract and owners

- Preparation / PendingApprovalState own the immutable operation set and arguments.
- ResponseAssembler passes the entire selected set, independent results, and missing
  inputs through one author/verification path. No recursive stripped approval board.
- Composer explains changes using supplied business facts; it does not create or
  expand an approval grant. Only a checked complete reply obtains the existing scope key.
- Generation receives a display projection of evidence, not trace identifiers.
  The unmodified evidence snapshot remains the source for verification and publication.
  Public knowledge citations are explicitly supplied; receipt IDs are not citations.
- A generation/verification failure retains progress and does not publish a usable
  approval challenge. Recovery regenerates presentation, never re-prepares a write.

## Steps

1. [done] Trace task20 outputs and response assembly / provider / verifier consumers.
2. [done] Remove split scope-card path; align shared presentation instructions.
3. [done] Separate model display evidence and public citations from provenance.
4. [done] Migrate tests and verify multi-operation, mixed input, failed presentation,
   publication binding and checkpoint behavior. Run no new paid business benchmark.
5. [in_progress] Record results and limits; commit scoped changes and push.

## Exit evidence

Author and verifier retain the same selected operation set; the public text has
one author and no parameter-card suffix. Scope survives roundtrip and approval
executes each operation once. Model failure cannot fabricate a bound challenge.
Display projection preserves business values and public citations, without
mutating original evidence or requiring an extra model invocation.

Implementation completion is separate from fresh real-model validation; this item
does not claim all historical workflow/context issues are closed.

## Review and verification

- Broad approval/response/publication/workflow suite with isolated PostgreSQL:
  **347 passed, 2 skipped**. Skips are the documented in-memory-ledger database
  scoping cases in `test_write_workflow.py`, not unavailable PostgreSQL.
- Claim verifier / answer verifier / interaction-context checks: **79 passed**.
- Final projection/native response/claim/history suite on isolated PostgreSQL:
  **107 passed, 0 skipped** after all review corrections.
- Independent fresh-context review identified the old-card serializer silent-null
  risk, a no-proposal prompt contradiction, and historical observation envelope
  metadata. All three were corrected at their owning boundaries and retested;
  independent recheck reported **21 passed**, no remaining concrete defect within
  those corrections.
- New tests exercise the real PostgresConversationEvidence envelope producer,
  complete and bounded history, public citation allowlist, full scope preservation,
  failed generation/review, revision with unchanged evidence, and SDK input capture.
- No paid model calls or new τ³ business run. Real-model fluency and task20
  before/after effectiveness remain unvalidated; this is implementation and
  contract/integration evidence, not an end-to-end business closure claim.
- Files: response assembly, shared approval instructions, provider display
  projection, verifier instructions, checkpoint preflight, focused tests and
  `docs/approval-response-continuity-2026-09-10.md`. Other dirty work is untouched.
