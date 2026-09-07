# Write-result preservation — bounded verification

## Proven cause

v17 task 0 executed `exchange_delivered_order_items` once and received the updated order (`exchange requested`, exchange fields populated, difference -16.63). Its official database score was 1.0. `_ToolPort` discarded `ToolResult.data`; the ledger and AgentResult retained only receipt metadata. The reply verifier therefore received the old read observations without the write observation.

Langfuse observation `67236896b59322a3`, trace source identified in replay-input.jsonl, confirms **pending_actions was empty**. The erroneous waiting-approval assertion came from historical assistant messages, not an uncleared pending approval at that point. Earlier statements attributing this specific failure to persisted approval state were too broad.

## Repair and scope

The existing tool-to-FactRecord adapter now handles committed writes and successful reconciliation observations. Facts are persisted in the existing PostgreSQL operation payload, returned by committed replay with original provenance/timestamps, and passed through existing AgentResult/checkpoint/response evidence. No new engine, database, provider route, or tool-specific reply rule was introduced.

Old operation payloads without facts remain evidence of the recorded action only; missing historical business output is not fabricated. A status service proves only the data it returns. Receipt commitment does not prove downstream settlement or physical delivery.

Read snapshots under different tool authorities are not automatically interchangeable. Deterministic order/refund replies now identify their observation time instead of asserting that a historical lookup is the current post-write state. The full records remain available.

## Validation

Final concentrated run: **181 passed, 2 skipped**, 73.38 seconds. Skips are the two ACTION/WORKFLOW in-memory combinations of the PostgreSQL scope-isolation test, not unavailable database tests. Suites: write_workflow, handoff_runtime, tau3_tool_binding, native_response_contract, response_assembly, controlled_refund_statements, target_http_postgres_e2e. Earlier runs exposed invalid new fixture JSON and obsolete exact-copy reply assertions; corrected in tests, not by weakening production evidence contracts.

- Parameterized contract tests cover ACTION/WORKFLOW, direct commit/reconciliation, memory/PostgreSQL reconstruction, heterogeneous JSON outputs, exact observation preservation, no repeated writes, checkpoint round-trip and response evidence.
- HTTP tests exercise actual approval -> committed result, replay and reconciliation through PostgreSQL, assert no remaining pending proposal in committed composition snapshots, and assert business facts are present.
- Noncommitted outcomes cannot carry committed business observations.
- Old-read plus committed-write template tests verify time-scoped wording in both supported locales.
- Independent fresh-context review found the template issue and confirmed its correction. This review does not certify complete customer E2E.

## Real-model component replay

`reply-replay/cases.jsonl` records one composition + verification using the original redacted Langfuse input, supplemented by the recorded official write result through the changed `_ToolPort`. No business tool was called. The author reports the exchange as executed and `exchange requested`; semantic verification returns PASS.

Limitations: this is reconstructed component input, not a fresh official task run. Original trace redaction collapsed product variant IDs and removed some fields; appended observation times were reconstructed within the recorded turn. The output repeats replacement prices/options and promises a return-instructions email not independently established by that redacted evidence. These details survive in prior dialogue. Therefore PASS is evidence of the execution-status regression only, **not whole-answer factual correctness**.

Three additional candidate checks using the same evidence rejected: denial of committed execution, invented completed refund settlement, and reversed payment direction. The last rejection also included a spurious historical-approval rationale; semantic judge reliability remains a separate open issue. These checks are development probes, not held-out scores.

## Status

Write-output preservation implemented. Full customer-facing closure remains open: independent policy evidence, source-preserving diagnostic redaction, judge overreach, and fresh full-task verification cannot be inferred from this repair or its test count. No additional business write was run for reply replay.
