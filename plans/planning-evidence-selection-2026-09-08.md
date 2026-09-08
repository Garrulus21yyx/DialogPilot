# R01: evidence-action selection, not another router

Start: e87b2ac, existing unrelated working changes preserved. User authorizes implementation and selection of fixed/dynamic few-shot guidance. ce-evaluation and planning-with-files guide this work; existing framework capture, planning provider and SDK remain the owners.

## Cause and positive contract

Observed DMV witness correctly resolves `Yes, it was` to an out-of-state sticker, but returns RESPOND saying it will search and asking extra personal details. The actual prompt already requires goals for investigation. Schema validity does not prove action selection. The development harness imports history into an empty state; it does not exercise persisted pending-input recovery.

ConversationAgent.plan owns semantic action selection. Provider owns model instructions and demonstration projection. Registry owns available capabilities; state owns binding/approval. Retrieval and downstream verification cannot compensate for a missing plan. Supported behavior: continue the outstanding information need using supplied conditions; retrieve rules when absent evidence blocks an answer but not the search; ask only when the subject/search itself cannot be determined; respond for social turns or evidence-sufficient explanations; no invented writes, identity or approvals. Examples are demonstrations, never current facts or runtime signals.

## Frozen experiment and adoption

- in_progress: scope/causal surface audit and frozen datasets before calls.
- pending: four development arms on original 12 DMV cases plus 6 ecommerce controls: current prompt; concise decision contract; same contract + 3 fixed examples; same contract + 3 dynamic examples. One call/case/arm, 72 maximum, DeepSeek v4 flash/NONE, 2048 output, SDK retries 0. No tool/answer API calls. Full actual requests and outputs saved after each call; failure retained. Existing baseline is not silently reused across input changes.
- Dynamic selection: installed LangChain SemanticSimilarityExampleSelector and InMemoryVectorStore, local BGE-M3 via existing sentence-transformers. Query comprises relevant history + current request (not `yes` alone). Separate curated demonstration data, no evaluation rows in its index. No new serving dependency unless dynamic wins.
- Fixed and dynamic use the same 3-example count and bounded input format. Compare actual tokens, selected IDs, protocol validity, knowledge plans vs conversational/clarification plans, lost conditions, unnecessary queries/questions and unauthorized actions. Human semantic review supplements deterministic action checks; tool calling alone is not success.
- Adoption: no new unsafe action; no material regression on social/ambiguous controls; improve correctly scoped evidence plans on the development failures. Prefer contract-only, then fixed over dynamic if quality equivalent; dynamic only for a demonstrable additional gain, with cost reported. Rejected candidates remain evaluation-only, not runtime alternatives.
- pending: selected single production path; properties/regressions across context transport, supported capabilities, state binding and response preservation.
- pending: 12 independently authored frozen acceptance cases, selected configuration and original baseline once each (24 maximum). No editing production based on acceptance output in this work item. Independent review of code and outputs. Total model budget <=96; failures are not rerun. If acceptance fails, retain honest non-closed status, do not ship an unproven default or invent a universal guarantee.
- pending: record results, limitations, commit/push; no retriever tuning, classifier retraining, new global LLM, extra semantic verifier or new state machine.

## Verification scope

Use existing deterministic tests for typed/pending input and approval lifecycle; add owner-level example/input and decision contract tests where needed. Real calls assess semantic planning only. No claim of whole customer-service E2E, official tau score, or fresh public benchmark accuracy. Independent acceptance is synthetic and held out from this selection, not representative production traffic.

## Development decision (before acceptance)

72 calls completed, original artifacts retained in `artifacts/eval/planning-guidance-dev-2026-09-08`. Baseline/contract/fixed/dynamic knowledge counts 5/9/12/13 of18; schema failures 1/2/0/1. These are routes, not answer correctness. All arms preserve social/ambiguous controls; fixed/dynamic add inferred changed-mind wording to the non-quality shoe control (record as semantic caveat, not exact preservation). Dynamic invents a future 60-day expiration condition in dfe698; its extra lookup is on a question with genuinely uncertain jurisdiction, so not established net semantic benefit. Fixed still mishandles ambiguous `do not` and fails to preserve a latest inspection question after an extension discussion. No new production branch is added for either.

Selected candidate for independent acceptance: existing prompt + decision contract + first3 fixed demonstrations, byte-identical to development arm. Dynamic not selected; avoid online embeddings/vector-store/selector. Freeze before opening model outputs for independent dataset (SHA256 e0dc8c4a7cc71863351a69e3b1fb88a5dbf7452fbfce33dbfd8ab7c01ce71594). Run baseline/fixed once each,24 calls maximum; production not yet changed. 34 local context/demo contract tests passed; not semantic proof.

## Few-shot rejection and final bounded confirmation

Independent review confirms fixed/dynamic add an unstated changed-mind condition on the shoe control. This violates the semantic preservation gate: reject both despite greater lookup counts. Independent acceptance24 shows identical baseline/fixed behavior;10 supported cases acceptable,2 expected record-discovery cases have no matching tool in the supplied Registry and remain recorded capability-fixture mismatches (not silently dropped failures). Neither result rescues few-shot adoption.

User requested an adopted simple solution; evaluate the already frozen contract-only candidate once more, without changing its text or creating more candidates. Fresh independent8 supported cases (4knowledge,2social,2ambiguity), baseline/contract once each16 calls. Total explicit maximum changes96→112 because first selected candidate failed semantic review; no model retries, no revisiting failed cases. The earlier acceptance set is consumed; this final confirmation is frozen separately before outputs. Require no new semantic/protocol failure against baseline on supported cases and preserve conditions/no actions; development gain must remain reflected in recorded evidence. If it fails, retain baseline and report non-closure; no further experiment in this work item. Regardless, public DMV residual failures and statistical generalization remain open. No production change yet.

## Final disposition

- done:112 calls and three zero-model input/trace audits; all failure records retained.
- failed adoption: confirmation02 contract invents policy_date2025-01-01 despite current-policy request; raw tool arguments and compiled command agree. Independent reviewer first missed optional arguments; main reviewer identified the error, reviewer rechecked full payload and withdrew acceptance. Additional product-filter membership is unverified, not silently scored correct.
- done selection: reject contract/fixed/dynamic. Keep existing one production path unchanged. No online example infrastructure or fallback added. Original R01 semantic gap remains open; no more model calls this work item.
- verified: local and isolated staged-tree checks each122 passed,1 external database test skipped; compressed artifacts are sufficient for all three trace audits. No dependence on unrelated working-tree changes. Report docs/planning-evidence-selection-2026-09-08.zh-CN.md distinguishes decision completion from semantic repair. Delivery is this experiment-only commit; commit/push result is reported in the handoff.
