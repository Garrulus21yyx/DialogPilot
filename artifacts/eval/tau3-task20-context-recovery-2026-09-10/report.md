# Task20 context-recovery regression

Run: 2026-09-10, HEAD a1ada68 plus manifest-recorded working-tree hashes.
One development attempt, seed 300, max_steps 80, unchanged model configuration.
No prompt/code edits during execution. Raw evidence retained alongside this report.

## Outcome

- Official ALL: 1; ENV: 1; ACTION: 1. Database match true; no evaluation errors.
- Normal user_stop, 54 trajectory messages, duration 99.85 seconds.
- modify_pending_order_items executed once, updating four item IDs in the target
  pending order; returned payment history records the gift-card charge of $71.96.
- User selected gift card, received the prepared action and approved it; the next
  turn executed the operation and delivered a completion reply.
- No recorded context-budget/step-limit terminal error. This run does not by
  itself prove a provider-overflow recovery was exercised.

## Comparison with retained source-continuity run

| Metric | Previous | Current |
|---|---:|---:|
| Trajectory messages | 82 | 54 |
| Official business-tool calls | 34 | 22 |
| Repeated identical name+arguments calls beyond first | 19 | 7 |
| Target write completed | No | Yes |

Repeat count is syntactic, not an assertion that every repeated read is avoidable.
Current repetitions: target order once extra, user once extra, four products once
extra each, same calculate expression once extra. No claim of eliminating reuse
defects or isolating causal improvement from a single stochastic run.

## Remaining observed quality issues

- Public approval/completion text leaks internal call identifiers as citations.
- Approval presentation uses raw item/payment IDs and stiff field labels, and a
  preceding sentence says it cannot proceed in this step before asking approval.
- Two response-review rejections occurred, followed by revisions. First concerns
  retained approval versus the selected pending-action presentation. Second
  concerns unsupported per-item prices and claims about processed orders.
- The official write result itself reports all four item prices/options identically;
  the final reply avoids asserting an itemized table. This report does not infer
  whether that upstream data discrepancy is an application defect.
- LiteLLM could not cost the provider-returned model alias. Cost completeness is
  not established; these log errors did not interrupt the business simulation.

Automated RCA now separates `business_status=PASS` from `quality_status=WARN`.
The quality lane detects seven exact unchanged read replays, public internal-reference
leakage, raw action-schema identifier fields, and two recovered response-review
rejections. Naturalness/tone still requires a calibrated semantic judge. HTTP/SSE,
multiple domains, heldout generalization and real-model compaction fidelity were not
tested by this one case.

Evidence: manifest.json, task-20.json, task-20-trajectory.json, rca.json,
inspection.json, simulator-calls/. The unified inspection contains 32 Langfuse
traces, 348 compact chain nodes, 23 causal events, zero error observations, two
expected control-status events, and complete usage for 35 generations (529,758
provider-total tokens, including 220,032 cache-read tokens). Session:
tau3-086de14654764de88a1ebe45937b6686.

The repeated-read quality root is now verified independently of business success:
the `agent_progress` novelty authority is scoped to one worker graph, so the
compacted continuation classified seven previously completed read identities as
`ALLOW_NEW_EVIDENCE`. See `quality_root_cause_status`, `quality_root_clusters`, and
the `cross_continuation_read_reuse` probe in inspection.json.
