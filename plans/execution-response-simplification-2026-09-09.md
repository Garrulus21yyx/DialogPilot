# Execution response simplification

Scope: pure action-status delivery, not arbitrary fact or monetary explanation.
The previous mode selector used model composition for multi-result sets even when
the only available content was authoritative operation receipts/statuses.

One small presentation module reads existing ResultBoard item/result pairs. It
does not infer completion from generated text, grant permission or change status.
Matching COMMITTED receipts establish submission, never downstream settlement.
Unknown outcomes remain unknown; local cancellation does not imply remote rollback.
ResponseAssembler binds deterministic text and the original evidence hash using
EXECUTION_STATUS_RENDERED (code rendering, not a model-review attestation).

No model author or verifier is called for supported action-only sets. Any missing
item, conflict, extra facts, candidate explanation, pending question/approval or
non-action work retains its existing path. No runtime state or checkpoint migration.
Tests span two languages, 1/2/5 operations, supported statuses, receipt association,
and exclusion of incomplete/mixed work. Existing Publication consumes the same
bound response contract; no additional publication path is added.

Important limitation: current write results often include full business facts or
a delegated continuation. Those do NOT take this shortcut. Monetary outputs and
general result replies have not been converted to deterministic customer views;
no claim is made of reducing their live model calls or fixing task22 by this change.
Inferring refunds, fees, settlement or prices from arbitrary returned keys would
be a semantic regression. Keep that remaining work explicit rather than silently
omitting facts or labeling all tool-derived prose as safe.

No paid model or business benchmark rerun. Targeted unit/integration validation
is recorded separately from measured live performance.

Validation: 144 tests passed with PostgreSQL enabled across execution presentation,
response assembly, TurnRuntime, current approval reply and publication snapshots.
git diff --check passed. User-owned unrelated edits remain untouched.
