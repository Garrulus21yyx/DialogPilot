# Execution response simplification

## Current implementation: tool-owned result presentation

Status: implemented and locally validated; not a claim of whole-dialogue or τ³ closure.

The shared cause of the repeated narrowing was treating **payload richness** as a
proxy for semantic risk: a receipt alone could be rendered, while adding the tool's
actual amount/address forced authoring and model verification again. The missing
contract was a public output view owned by the capability, not another reviewer.

The positive contract now is:

- ToolDefinition declares public field paths, localized labels and, for money,
  currency and unit scale. Signed charge differences are explicitly declared;
  positive means additional charge, negative means refund, zero means no difference.
- The existing tool-to-FactRecord conversion preserves the raw result and provenance.
  The renderer accepts the declared producer, authority and output-schema version.
  A submitted action's displayed facts must bind to its matching receipt, not an
  earlier query snapshot. No LLM supplies these bindings or public field labels.
- ACTION and governed WORKFLOW result sets use the same renderer. Known output
  fields, including amounts and addresses, do not call either author or judge.
  A candidate's internal prose is not published in this path.
- ResultBoard still owns progress/coverage, the operation runtime still owns
  receipts, and Publication still owns the only public commit/delivery path.
  Result fields are presentation metadata, not a second execution authority.
- Unknown projections return no deterministic presentation and use the existing
  explanation path. Missing inputs, approvals, conflicts and independent goals
  are preserved. In particular, identical facts do not prove that a delegated goal
  is redundant; this change does not suppress such a goal.

Current registrations cover native refund submission, shipping-address changes
and order cancellation; retail API account/order address changes, exchange request
difference, cancellation and return request status. Registrations are keyed by API
contracts, not task IDs or benchmark answers. Exchange sign convention is taken
from the retail tool's `new_price - old_price`; request submission is not settlement.
Unregistered payment/item changes and novel explanations are not claimed simplified.

Native registered output versions were aligned with the existing ToolManager
manifests for the three public views. The environment adapter now propagates its
declared output version through ToolResult instead of leaving it blank. These
declarations participate in the registry fingerprint. Existing prepared work must
follow the existing pinned-version policy; this change does not migrate approvals,
rewrite historical facts or replay business writes. Checkpoint shape is unchanged.

Alternatives considered: another model deciding whether to review would add a
decision layer; rendering arbitrary JSON would expose private/internal fields and
guess units; dropping repeated delegated facts could discard an independent answer.
The selected bounded change adds no runtime, dependency or model call.

Reference checked 2026-09-09: [LangChain guardrails](https://docs.langchain.com/oss/python/langchain/guardrails)
distinguishes deterministic from semantic model checks. The tool-owned presentation
choice is our engineering application of that distinction, not a framework mandate
or a SOTA performance claim. The tool-output skill guided placing presentation
semantics beside capability definitions rather than in the conversation model.

Validation covers generated sign/unit/currency combinations, both locales and
execution modes, schema/provenance mismatches, native manifest agreement, retail
ToolManager calls, retained checkpoint results, unchanged independent goals and
no author/judge calls. Existing PG integration suites remain the delivery check.
No paid model run, throughput measurement or new τ³ success score is claimed.

Final validation: **264 passed**, PostgreSQL enabled, across
`test_response_assembly`, `test_execution_presentation`, `test_result_field_presentation`,
`test_turn_runtime`, `test_publication_state_snapshot`, `test_approval_conversation`,
`test_tau3_tool_binding`, `test_checkpoint_takeover`, `test_customer_operations_tools`
and `test_postgres_publication`. The native integration test submits a refund through
the real ToolManager/PG service and presents its 39900 minor units as 399 CNY with
no author or judge configured. Its initial test assertion incorrectly used the
upper-case operation-ledger status for the lower-case ToolEffectStatus; the test
now consumes the proper enum. No production normalization workaround was added.
`git diff --check` passed. User-owned unrelated work is excluded from this commit.

## Historical first stage (633e3a6)

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
