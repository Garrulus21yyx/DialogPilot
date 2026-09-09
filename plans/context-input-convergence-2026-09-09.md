# Context input convergence

IMPLEMENTED; offline preservation/budget validation passed; full-task model validation
remains open. User requests implementation plus before/after measurement on saved
inputs. No paid model calls or tau rerun: provider balance unavailable. HEAD at
start f1ae53b; preserve unrelated dirty RAG/archive changes.

Mechanisms: review serializes full tool schemas and repeats candidate inside
working history; fixed runtime prompt is protected alongside recent tool batch;
nonshrinking summary above a trigger is misreported as capacity failure despite
passing actual admission. Existing framework summary/clearing remains the single
editing mechanism. No arbitrary increase to limits or business-specific filtering.

Positive contract: stage-specific review projection preserves goal/constraints,
policy, capability meaning, source evidence, receipt and candidate once. Keep complete
tool schemas: their descriptions/enums may carry business meaning not duplicated in
the description. Do not add another parameter-validation owner. Projection does not
mutate original working messages/checkpoints. Context
triggers request editing; only actual consumer capacity decides admission. An
unhelpful summary cannot invalidate otherwise fitting original edited context.

Plan: capture available historical inputs (read-only Langfuse if necessary),
measure fixed vs working context, implement at review/compaction owners, exercise
preservation and budget properties plus SDK/PostgreSQL recovery, publish counts
with source hashes and limits. Independent review required before closure.

Acceptance: identical input pairs, unchanged token estimator/model budgets; source
facts/constraints/receipt/candidate retained; fewer estimated tokens without paid
summaries; typed over-capacity remains enforced; a summary trigger alone never
rejects an otherwise admissible input. Semantic quality remains unverified.

## Implementation and ownership

- DomainOutcomeReview owns its model-facing projection. SDK messages remain intact.
  Exact final-candidate duplicates use a local reference in the same request; JSON
  message/text-block wrappers become structured values, avoiding repeated escaping.
  No recursive parsing of business strings. Duplicate keys, nonfinite numbers and
  decimals that cannot round-trip numerically retain their original text.
- ContextCompaction still uses LangChain summary/clear components. Trigger thresholds
  request editing; actual actor/reviewer capacities alone decide admission. A summary
  with no savings is discarded. Exhausted summary-call budget skips optional summary
  for fitting input; real overflow still raises the capacity failure. Pinned task and
  latest paired tool batch remain protected. No additional summarization runtime.
- No removal of policies, schemas, receipts, alternatives or business evidence.
  No new dynamic-retrieval/planning call or arbitrary budget increase.

## Evidence (2026-09-09)

Read-only Langfuse capture of task 19/20 sessions found two review payloads in task
19; task 20 yielded no review payload. Empty capture is not a successful comparison.
Final local report:
`artifacts/eval/context-projection-verified-before-after-2026-09-09/report.json`.

| Historical input SHA-256 | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| cf1941817b629dcee87348fb1c30eecc14c22ef3d4d4542c22dbcaab272a9351 | 22686 | 20821 | 8.2% |
| fb4da326ce1a92037b6dfe3bd15debf00d78ea1cf0a3686fdc595738e65f52de | 21102 | 19603 | 7.1% |

These are identical captured payload pairs using the unchanged approximate SDK
counter. Totals cover request payload only, excluding system/output-schema overhead;
not provider billing measurements. The actual admission counter continues to measure
the entire request. Both preservation audits pass, retaining capabilities including
full schemas and using Decimal comparison for numerical preservation.

Checks: 124 passed / 7 environment-dependent skipped across context, admission,
operation-plan, approval and framework-agent tests; separate real PostgreSQL context
and checkpoint run: 54 passed. This includes source nonmutation, unsafe JSON forms,
recent evidence, repeated no-growth admission past summary budget, missing pinned
message, true capacity failures, archived original reopening and review-failure resume.
Final projection-only check after the exponent-range guard: 23 passed. Reviewer's
independent bounded check: 57 passed, 1 PostgreSQL case deselected. Exponent overflow
and underflow are retained verbatim rather than leaking numeric conversion errors.

Independent fresh-context review found precision loss, escaped-surrogate transport
failure and summary-budget exhaustion; corrected with transport, sequence and
preservation tests before delivery. Initial empty
capture and intermediate projection reports remain local and are not final evidence.

Not proven: the five historical capacity failures disappear, or task outcomes improve.
Those failed review calls did not reach the model and are not the two captured inputs.
No paid tau rerun was made (provider HTTP402); no write was replayed. The larger
protected task payload and repeated schema/description overhead remain measurable
optimization opportunities, not permission to remove required policy/evidence.

Next: reproduce complete failed invocation inputs from retained state when available,
measure full-request admission against the same budgets, then fresh model validation
when provider availability permits. Do not resume unrelated strategy experiments.
