# Verifier value probe

Pre-registered: 12 synthetic development cases, six correct/incorrect pairs with
identical evidence within each pair. Frozen expected accept/reject labels, one
call per candidate, current VERIFIER profile, no retries or prompt edits.
Reuse scripts/run_claim_verification_replay.py and production verify_claims.
No business writes, reply rewrites or full-publication claims.

Compare pass-through (all 12 delivered) against verifier acceptance. Report wrong
answers blocked, correct answers rejected, API/schema failures, model tokens and
latency. Policy/compatibility evidence is supplied as facts to isolate semantic
judgment from retrieval/citation mechanics. Ordinary questions are deliberate
controls, not a proposal to add online review to that path.

Adoption criterion: this small probe cannot justify a global gate. Any false
rejection must be reported; zero errors only supports a larger representative
experiment. Synthetic balance is not production error prevalence. Labels are
authored by the coding assistant from explicit evidence, not independent humans.

Capture: artifacts/eval/verifier-value-probe-2026-09-09.jsonl
Output: artifacts/eval/verifier-value-probe-2026-09-09

## Result

Runtime HEAD f85d116 with existing unrelated user edits; no production changes.
Configured verifier deepseek-v4-pro, reasoning none, max output 4096; exactly 12
calls through production verify_claims, captured by existing FrameworkCapture.

| Pair | Correct answer accepted | Incorrect answer rejected |
|---|---|---|
| Return-policy exception | yes | yes |
| Similar-model compatibility | yes | yes |
| Partial business success | yes | yes |
| Refund versus charge | yes | yes |
| Color clarification versus false completion | yes | yes |
| Unknown arrival versus guaranteed arrival | yes | yes |

Pass-through baseline delivers all six known errors and six correct answers.
Verifier gate accepts six correct answers and rejects six errors: false accepts 0,
false rejects 0, API/schema errors 0. Rejection is not correction or completion;
rewrite behavior and user experience were not tested. Positive labels were never
included in model input. No retries or sample selection after outcomes.

Added latency per answer: median 2023 ms, range 1384–3817 ms, summed model-call
duration 27700 ms. Usage: 19921 input tokens, 1053 output tokens (20974 total).
No money estimate: provider billing/cache discounts not verified.

Review of feedback: amount_bad correctly identifies refund/charge inversion,
but also characterizes future-tense charging as an assertion of execution, which
is not independently established by the candidate wording. Correct binary rejection
does not prove every suggested reason is sound; blind revision remains risky.

Conclusion: useful detection of explicit semantic contradictions in this controlled
sample, not production accuracy or evidence that every reply should be reviewed.
Some checks (status, amount direction) should primarily use authoritative data and
rendering. Keep ordinary questions on their existing no-review path. Test ambiguous
real outputs and realistic error prevalence before changing online review scope.
This probe does not establish a quality gain from automatic rewriting, retrieval
quality, complex long-context reliability, or full Publication verification.
