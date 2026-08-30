# DialogPilot Reviewer C sealed stateful contract

Status: `provisional`; split: `heldout`; reviewer: `reviewer-c-independent`; license: `CC0-1.0`.

This dataset is a one-time independent acceptance holdout for target commit `7f5797e`. Once viewed and run by Reviewer C it is consumed evidence; if used for later repair development it must be relabeled as a consumed regression. It is not human Gold and cannot establish production accuracy.

## Authority and evidence model

| Role | Contract |
|---|---|
| authoritative owner | Production component that owns the domain transition: `MemoryManager`, `HybridMemoryRetriever`, `KnowledgeBase`, `MCPToolManager`, `AnswerVerifier`, `CoverageGate`, `ResultSynthesizer`, `TicketService`, or the HTTP publication boundary. |
| producer | The production owner invocation that emits raw observations, receipts, audit records, or typed outcomes. |
| consumer | The next production boundary and the independent grader. A consumer may project evidence but cannot invent owner execution. |
| persistence boundary | Redis working memory, Chroma episodic/profile/knowledge collections, SQLite tickets, and the tool audit store. In-process deque audit is explicitly non-durable. |
| public projection | HTTP response, public agent outcomes, memory retrieval evidence, tool audit projection, and RAG hit projection. Candidate and internal errors are non-public unless the verifier returns PASS. |
| typed error/outcome | Closed enums/results for verification, task coverage, tool call status, side-effect status, finalize retry, and fixture execution failure. Unsupported states fail closed. |
| fixture | Receives frozen scenario/setup/inputs only, directly invokes production owners, and returns raw observations. It never receives `expected`. |
| scorer | Runs only after actual observations are sealed, derives boolean assertions independently, enforces case/prediction bijection, and treats unknown actions as `coverage_gap`. |
| documentation claim | Must not exceed executable owner evidence. In particular, no idle-timeout archive, durable tool audit, generic owner-provenance proof, adaptive routing with one instance, human Gold, or production-accuracy claim is inferred. |

## Positive supported contracts

- Fixture boundary: expected cannot enter actual production; scenarios are immutable; fixture exceptions, bad result types, missing/duplicate/extra predictions, non-boolean assertions, and unknown actions fail closed. Owner provenance requires independent instrumentation and is distinct from expected-copy isolation.
- Memory: token-triggered compression keeps a bounded structured summary, archives raw messages, uses optimistic concurrency, and exposes explicit idempotent finalize for short sessions. Hybrid retrieval is user-scoped vector + BM25 + RRF + recency over the semantic/lexical candidate union. No idle-timeout contract is asserted.
- RAG: chunk token budget, structural boundaries, overlap, stable chunk identity, parent-document projection, and post-ranking parent diversity remain aligned. Legacy chunking versions require an explicit reindex/detection gate.
- Tools: host-owned approval, closed call states, separate side-effect algebra, one correlated redacted terminal audit per started invocation, and explicit business receipts. Timeout/cancelled writes remain `outcome_unknown` absent proof. Durability and write-retry idempotency are required claims, not assumed from a deque or call ID.
- Publication: PASS alone publishes candidate. All verifier faults and unsupported outputs yield typed non-publishable results; rejected candidates are absent from response, public outcomes, and memory. Handoff creation is persistent, retryable, and idempotent by request.
- Coverage: task IDs are authoritative. Required missing/timeout, duplicate, unexpected, partial, and all-failed outcomes remain explicit; incomplete coverage blocks verification. One instance per agent type cannot establish adaptive choice.

## Executability

Each case contains concrete `setup`, `action`, `inputs`, `invariants`, and `failure_condition`. The independent runner dispatches every action to a fixture that invokes the named production owner or boundary. Raw observations are captured before the scorer opens expected assertions. Mutation tests alter runtime behavior with monkeypatching only and must restore it after each mutation.

## Blindness disclosure

Before sealing, Reviewer C did not open the explicitly listed Reviewer B evidence files, Reviewer B dataset directory, or `evaluation/fresh_stateful_fixtures.py`. However, a broad symbol-location command accidentally displayed existing stateful fixture names and short test assertion snippets, including a Reviewer B-labelled test name. Therefore strict “zero exposure to any old adversarial test information” is not claimed; this is a protocol contamination and independently prevents an `accept` decision.
