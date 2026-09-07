# Write result evidence preservation

Status: implementation and bounded contract verification complete; full customer-facing closure not yet verified.

## Evidence and cause

The v17 task 0 official write returned the updated order and official database scoring passed. `_ToolPort` reduced that result to receipt metadata; `WriteToolOutcome`, `OperationRecord`, and `_committed_result` had no business-output field. Reply authoring/verification retained earlier read observations without the new write observation. Historical assistant wording was then treated as evidence against execution.

## Positive contract

- The governed tool adapter owns conversion of authenticated tool output into the existing `FactRecord` contract. A committed write carries that output and provenance through the operation ledger, AgentResult, checkpoint, and response evidence.
- Replaying a committed operation returns the same observation, without a new tool call or a new observation timestamp.
- Reconciliation preserves only what the status service actually returns; it cannot manufacture an updated entity or infer settlement from acceptance.
- Author and verifier receive the same evidence. A committed receipt establishes execution of its action, not every downstream physical or financial consequence. Earlier read/history observations do not establish non-execution after that receipt.
- No per-order/per-tool response rules, new persistence system, or second execution path.

## Work

1. Implemented: preserve observations using existing fact conversion and SDK serialization. The actual failed trace has no pending approval; history was misinterpreted by the judge.
2. Verified: direct success, replay, reconciliation, PostgreSQL reconstruction, checkpoint, reply evidence, actual approval lifecycle and observation-scoped templates. Concentrated suite: 181 passed, 2 in-memory-only scope skips; PostgreSQL and HTTP cases executed.
3. Reviewed: independent review identified and confirmed the deterministic template correction. Fixed-case reply-only model replay recognizes execution; redacted-input and unsupported-detail limits prevent whole-answer closure. See artifacts/eval/write-result-evidence-2026-09-07/analysis.md.

## Non-goals

No new automatic read-back loop, inferred business-object mapping, benchmark policy change, or unconditional assertion that a committed action completed settlement.
