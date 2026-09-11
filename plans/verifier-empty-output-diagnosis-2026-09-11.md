# Verifier empty output: bounded diagnostic

Status: diagnostic completed; reviewer-context repair implemented and validated
as described below. Historical empty HTTP output remains unproven.

Observed: task20 observations 152446afc5a316b6 and a001406ceebf3fe4
contain empty tool input before application assessment validation.

Hypotheses: excessive/conflicting input; unnecessary result envelope; provider
tool-call conversion or generation failure. Missing strictness only explains why
invalid parameters are possible, not why these specific calls were empty.

Budget: four pure verifier requests, DeepSeek v4 Pro, thinking disabled,
temperature 0, output cap 4096, no automatic retries or business tools.
Development diagnostic only: saved Langfuse inputs are already privacy-filtered,
so replay is not an exact historical reproduction.

Conditions:
1. Recorded system/input, existing result-envelope schema.
2. Same system/schema, tiny unambiguous input.
3. Recorded system/input, flat assessment schema.
4. Tiny input and short system instruction, existing envelope schema.

Capture HTTP response before LangChain normalization and compare SDK tool input.
Measure schema validity, response shape, stop reason, tokens; quantify recorded
input sections and duplicated facts. Preserve every result, including failures.
No variant is adopted from four samples; no causal conclusion from one success.

## Results

All four HTTP responses contained populated tool input, matching the SDK parsed
input; all passed their respective schema. No automatic retries were used.

| Condition | Schema | Assessment | Output tokens |
|---|---|---|---:|
| Recorded system/input | existing envelope | REJECT | 480 |
| Same system, tiny input | existing envelope | PASS | 52 |
| Recorded system/input | flat | REJECT | 419 |
| Short system, tiny input | existing envelope | PASS | 52 |

Recorded system: 7,385 characters. User payload: 33,758 characters. Serialized
existing tool/schema: 472 characters (two booleans, an issue list, one envelope).
Context sections: policy 4,668; facts 18,898; outcomes 3,061; user_context 5,724
characters. Facts: 11 entries, 9 exact unique entries. These measurements prove
redundancy/size, not that size caused the historical empty output.

The original-input assessment illustrates a separate semantic boundary failure:
it says that asking the payment choice before preparing is allowed, then rejects
the same payment choice for lacking a prepared action. It also treats candidate
prices as requiring preparation receipts. Those are different from execution
permission and cannot be merged into one prepared-action requirement.

The flat-schema assessment also contained a demonstrably incorrect fact check:
it claimed the most expensive available keyboard costs 269.46 instead of 272.33.
The exact recorded input contains variant 1151293680, available=true, price=272.33;
the old item price is 235.13, supporting the stated difference 37.20. Flattening
the schema did not remove semantic verification errors.

Historical empty output remains unexplained below the SDK-message boundary:
there is no stored original HTTP response to distinguish historical generation
from compatibility conversion. Fresh HTTP capture shows neither input loss nor
empty output in these four calls. The missing strict-output guarantee is a risk
factor, not a demonstrated explanation of those two historical events.

Official compatibility reference inspected 2026-09-11:
https://api-docs.deepseek.com/guides/anthropic_api
It documents output_config support only for effort. Do not assume switching
LangChain to Anthropic json_schema supplies strict output on this endpoint.

Conclusion: output schema complexity is not established as the cause. The
demonstrated input/semantic defects are broad, repetitive verification context
and conflation of candidate explanation with prepared execution. A coherent
future change must separate evidence support from execution authorization and
test both positive inquiry/candidate cases and unsupported execution promises.
Do not add product-name branches, silently pass empty results, or adopt any
prompt/schema variant from these four development calls.

## Implemented reviewer contract

The reviewer now receives a dedicated deterministic view of the same source
snapshot. It retains facts and provenance, all outcomes/coverage, receipts,
pending actions, requested inputs, historical evidence, accepted query scope,
conversation constraints and policy excerpts. Exact duplicate facts are removed
and every outcome fact index is remapped. Agent descriptions, control revisions,
planner directives and tool navigation are not reviewer instructions.

The single static system instruction defines evidence support and response
coverage, separates candidate comparison/input requests from approval/execution,
and treats runtime state as authoritative. It explicitly retains CURRENT and
NOT_REUSABLE source semantics. Dynamic approval-presentation prompt appendices
are removed from verification; composer behavior is unchanged. The three output
fields and full-source assessment binding are unchanged. No new model stage or
runtime is introduced. Existing finite malformed-output recovery remains.

Independent review identified public conversation policy and accepted query
arguments as evidence that must survive projection; both are retained and tested.
The system prompt is now 2,735 characters versus 7,385 in the recorded call.

Pure model acceptance: three development cases were run before the final review
corrections, then the same three were rerun on the final version, without business
tools or automatic retries. All six had valid HTTP and parsed tool input.
Final results (DeepSeek v4 Pro, same evidence derived from privacy-filtered trace):

| Candidate | Expected | Actual | Input incl cache | Output |
|---|---|---|---:|---:|
| Original comparison and payment choice | PASS | PASS | 10408 | 52 |
| Fabricated completed upgrades and charge | REJECT | REJECT | 10149 | 322 |
| Total changed from 75.30 to 750.30 | REJECT | REJECT | 10408 | 118 |

The original-input baseline in the diagnostic used 13,091 input tokens including
cache; the final original-candidate case used 10,408 (about 20.5% less). This is a
single development example, not a general efficiency or accuracy benchmark.
False-execution rejection still contains unnecessary secondary inferences; do
not label all free-text feedback calibrated. The exact historical empty output
was not reproduced and its lower-level cause is not claimed fixed.

Regression scope includes projection permutations, caller immutability, fact-index
remapping, retained approvals/query arguments/source validity, original snapshot
binding, structured transport, semantic dimensions, revision and delivery paths.
An expanded conversation-response test also fails with the pre-change HEAD
reviewer modules loaded: it expects Completed for an existing wait but receives
NeedsInput. That separate lifecycle expectation is not changed by this repair.
Final focused regression: 205 passed, 3 warnings. Independent final reviewer:
69 passed, no demonstrated remaining evidence-loss blocker. This commit contains
the reviewer projection/prompt and tests only, not concurrent evaluator changes.
