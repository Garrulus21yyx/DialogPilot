# Project walkthrough

## 30-second version

DialogPilot is a Python/FastAPI multi-agent customer-support backend. It reads
Redis and ChromaDB memory, classifies intent using an LLM, local semantic
similarity, and rules, retrieves business knowledge through a reliable tool
layer, and uses a token-budgeted context assembler with bounded structured
rolling summaries. It routes requests to general, technical, or billing agents. Complex
requests can execute agents concurrently. Before returning a response, a typed
result synthesizer isolates per-Agent timeouts, preserves partial success, and
detects cross-Agent conflicts. A verifier then allows only explicitly passed answers to be published; failures are
escalated deterministically into an idempotent SQLite-backed human ticket with
a typed lifecycle and audit history. Prometheus monitoring and LLM-as-Judge
evaluation close the online and offline feedback loops.

## Engineering decisions

### Why combine three intent signals?

The LLM handles natural-language ambiguity, local character n-grams provide a
deterministic semantic fallback, and patterns are strong for order IDs, error
codes, refund language, and amounts. Concurrent execution controls latency and
weighted voting exposes source scores for debugging.

### Why retrieve only for business intents?

Knowledge retrieval improves factual business answers but can pollute greetings
and handoff requests. Intent-owned retrieval gating reduces irrelevant context,
latency, and reranking cost.

### Why multiple agents?

General, technical, and billing prompts encode different response policies.
The orchestrator owns selection and can run primary/supporting agents in
parallel for mixed requests such as login failure plus duplicate billing. Each
execution becomes a closed `SUCCESS / TIMEOUT / ERROR` outcome, and one
ResultSynthesizer owns deduplication, conflict detection, output order, and
partial-success escalation.

### Why compress by tokens instead of message count?

Message count does not predict model input size. DialogPilot estimates prompt
tokens, preserves the recent raw turn, and replaces old memory with a bounded
structured summary. An optimistic Redis transaction prevents an LLM summary
generated from a stale snapshot from overwriting messages that arrived during
compression. Prompt assembly keeps retrieved data in tagged sections and real
conversation turns in the message sequence.

### Why fail closed at verification?

The verifier owns whether a candidate answer is publishable. Treating parser or
model failures as success would silently bypass that boundary. A closed outcome
algebra makes unsupported states explicit and routes them to a safe handoff.

### Why does the ticket service own its state machine?

The API and Agent orchestrator can request a handoff, but only TicketService
owns identity, idempotency, persistence, legal transitions, and event history.
That prevents controllers from inventing states and ensures retries do not
create duplicate tickets. SQLite keeps the portfolio deployment simple while
the contract is narrow enough to migrate to PostgreSQL when horizontal writes
are required.

## Honest measurement language

Use results from `/eval/run` only with the dataset size, model, date, and runtime
configuration. Built-in cases demonstrate the evaluation pipeline; they do not
establish production accuracy or latency.
