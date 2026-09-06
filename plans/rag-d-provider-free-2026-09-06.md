# RAG D: provider-free first, measurable full-chain optimization

Baseline: 5b60455. User authorized implementation, local experiments, incremental commit/push. No external inference API calls in this phase. Preserve preexisting workspace changes.

## Positive contract
Each experiment freezes source corpus, source-level evidence labels, case split/consumption, model/index identities, route depth and output budgets. Source parsing, chunking, embedding, query, retrieval, fusion, reranking, packing and actual model-visible serialization have separate measured boundaries. Cached artifacts are reused only when their inputs and versions match. Public regression and explicitly synthetic Chinese ecommerce development cases are present from the beginning. Heldout is never used to select weights. Offline replay is not represented as a live Conversation Agent or final-answer accuracy measurement.

## Work
1. complete: inventory reusable datasets/models/captures, freeze a reproducible provider-free development baseline and stage instrumentation.
2. in_progress: run deterministic parsing/chunk/provenance/serialization diagnostics, local retrieval and fusion replay; diagnose losses and select bounded fixes.
3. implemented and verified opt-in (production load benchmark pending): implement bounded parallel lexical versus query-embedding+dense retrieval with consistent identity, deadlines, concurrency and typed failures; measure equivalence and latency.
4. in_progress: compare fixed, corpus-type and lightweight query-adaptive fusion on development data only; add hierarchy/chunk/model variants only where failures justify them.
5. pending: provider-free regression/heldout acceptance and independent review, report wins/regressions/costs; commit/push each verified coherent batch.
6. pending external phase: production-provider query/generation validation only after an explicit measured budget is established. No final-answer gains claimed from local retrieval metrics.

## Exit criteria
Reproducible commands and checksums, per-case stage evidence, paired improvements and harms, resource/latency measurements, valid split provenance, and limitations must agree. Do not sum overlapping test selections or conflate pipeline replay with live Agent execution.

## 2026-09-06 development checkpoint
20 calibration cases, then actual 56 development cases (36 public groups +20 synthetic;55 total groups). Local CrossEncoder +2/56 at current weights, no final-answer measurement. Smaller chunks +3/56 with one harm; adaptive fusion grouped CV ties fixed at44/56. PG microbenchmark parallel slower, so opt-in only. Root diagnostic: 5/6 candidate failures already have gold parent in first5. See docs/rag-d-provider-free-2026-09-06.zh-CN.md. Full D not closed.

Parent-child bounded development replay completed: current-weight candidate50→52/56; tool-message44→44/56 after local CE. No production promotion. Independent review passed pool/source/group invariants; corpus-based CV is diagnostic, not production routing. First checkpoint626a22d pushed and verified. Additional artifacts/reproducible failure attribution follow in second checkpoint.

## Acceptance preparation (before inference)
Audited16 local cases.jsonl captures across repository and /tmp, excluded403 unique Doc2Dial groups, froze200 remaining official-test conversations (50/domain). No unconsumed eligible long-document cases remain in audited pool; acceptance coverage is short/medium only. Fixed512/64/history/flat/weight0.25, same candidates, no reranker vs local CE; no test-time grid. Dataset and model identities pinned in artifacts/eval/rag-d-acceptance-2026-09-06/acceptance-contract.json. Freshness limited to explicitly inventoried local datasets. Implementation and boundary tests prepared; inference pending.

Acceptance preflight failed before inference: plain-text punctuation falsely became Markdown atomic block. Fixed source_type propagation at DocumentChunker + ingestion/evaluation callers, bumped chunk schema; PG9 pass, local structure/replay/text42 pass. 488documents/1469chunks/340goldspans now project with100% containment. No heldout model scores observed yet. Preserved failed attempt and contract amendment; predeclared primary paired ToolMessage gain, exact two-sided McNemar p<.05, identical candidate pools, provenance/containment/API gates. Full goal remains open.

Completed first fixed public acceptance run:200cases, candidates183/200, ToolMessage169→171/200,8rescue6harm, exactpairedp=.79052734375. Predeclared EVIDENCE_QUALITY_NOT_DEMONSTRATED; no reranker production promotion.200cases now consumed. Independent review verified format propagation,800generated chunk invariants, result arithmetic and contract/provenance; local80 testbatch plusadditionaldecisionnegative testpass, PG9pass. See docs/rag-d-local-acceptance-2026-09-06.zh-CN.md. Full goal remains active: actualAgentquery, sameproductionentry, ecommerce semantics and finalanswer validation remain.

## Real planner local model calibration
Downloaded pinned Qwen2.5-3B-Instruct revisionaa8e72537993ba99e69dfaafa59ed015b17504d1 to local HF cache; no external inference. Real ConversationAgent/AnthropicConversationPlanningProvider logic executes through evaluation-only local text transport, preserving context/entity bindings/registry/validation. Three20case synthetic development runs completed. Original18outscope2invalid; schema+policy clarity17outscope3clarify; experimentalfewshot12clarify4resolved2outscope2invalid. Local3B not sufficient for reliable planning; no end-to-end success claim. Missing-field schema payload repair retained, fewshot prompt removed from production and saved as explicit evaluation override. Need broader-capacity local/provider evaluation and actual retrieval/answer continuation; goal remainsactive.
