# Project walkthrough

## 30-second version

DialogPilot is a Python/FastAPI multi-agent customer-support backend. It reads
Redis and ChromaDB memory, classifies intent using an LLM, local semantic
similarity, and rules, retrieves business knowledge through a reliable tool
layer, and uses a token-budgeted context assembler with bounded structured
rolling summaries. It turns requests into scoped tasks owned by general,
technical, billing, or account-security agents. Independent tasks execute under
one request deadline and max-Agent budget. A coverage gate proves that every
required task has a closed outcome before a typed result synthesizer preserves
useful partial evidence and detects conflicts. A verifier then allows only explicitly passed
answers to be published; failures are
escalated deterministically into an idempotent SQLite-backed human ticket with
a typed lifecycle and audit history. Prometheus monitoring and LLM-as-Judge
evaluation close the online and offline feedback loops.

The HTTP boundary derives identity from a verified JWT Principal rather than a
caller-supplied user ID. A dedicated, tool-free escalation worker prepares
human handoff evidence. Chroma storage mode is explicit and observable, and
short conversations are idempotently archived before their Redis TTL can erase
the only raw copy.

Within each scoped Worker, a bounded ReAct loop may select only tools exposed by
its allowlist. ToolManager—not the model—owns risk, approval, execution, bounded
writeback, and redacted audit. A request TraceId links HTTP, ReAct steps, and
tool calls. Episodic retrieval stores raw chunks and fuses BM25, vector, and
recency ranks, avoiding the precision loss caused by searching only summaries.

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

### Why task-aware multiple agents?

General, technical, billing, and account-security prompts encode different
policies and risk boundaries. The orchestrator owns a TaskPlan rather than only
an Agent list: each required task has an ID, Owner, scope, risk, and completion
criteria. Workers share an execution window and finish as `SUCCESS / TIMEOUT /
ERROR / BUDGET_EXCEEDED`; CoverageGate owns completeness and ResultSynthesizer
owns deduplication, conflict detection, output order, and partial evidence.

### Why compress by tokens instead of message count?

Message count does not predict model input size. DialogPilot estimates prompt
tokens, preserves the recent raw turn, and replaces old memory with a bounded
structured summary. An optimistic Redis transaction prevents an LLM summary
generated from a stale snapshot from overwriting messages that arrived during
compression. Prompt assembly keeps retrieved data in tagged sections and real
conversation turns in the message sequence.

Compression is not the only archive trigger. The conversation-finalize API
upserts deterministic per-message episodic IDs and then CAS-clears Redis; an
archive failure or concurrent message preserves working state for retry. User
profiles use one deterministic per-user record with version metadata rather
than an unordered “first row” query.

### Why combine deterministic planning with bounded ReAct?

The outer TaskPlan owns required work, risk, deadlines, and coverage; that keeps
the customer-support workflow reproducible. ReAct is limited to tool selection
inside one task Owner. This gives Workers observation/action capability without
allowing free-form delegation to erase task identity or bypass authorization.

### Why hybrid long-term memory?

Structured summaries are useful for bounded prompts but may drop exact order
IDs and error codes. DialogPilot therefore retrieves raw episodic chunks using
BM25 and vector search, fuses their ranks with a small recency signal, and
returns source/rank evidence. Retrieval quality can be measured with Recall@K,
MRR, and nDCG instead of judged from one fluent answer.

### Why fail closed at verification?

The verifier owns whether a candidate answer is publishable. Treating parser or
model failures as success would silently bypass that boundary. A closed outcome
algebra makes unsupported states explicit and routes them to a safe handoff.

The verdict also closes the online routing loop without conflating provider
availability with answer quality. `PASS` and `REJECT` observations update a
sample-aware EWMA for the exact producer Agent instances; `UNKNOWN` is recorded
as verifier infrastructure state and does not lower Agent quality.

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
