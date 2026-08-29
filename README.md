# DialogPilot

DialogPilot is an asynchronous multi-agent customer-support backend built with
Python and FastAPI. It combines intent recognition, retrieval-augmented
generation, dynamic business skills, layered conversation memory, observable
agent routing, and a fail-closed answer-verification boundary.

## Why this project exists

A single-prompt chatbot mixes routing, knowledge access, memory, and response
generation into one opaque operation. DialogPilot gives each concern a clear
owner and exposes the routing and verification decisions in the API response.

## Request flow

```text
POST /chat
  -> load Redis working memory and hybrid Chroma/BM25 episodic memory/profile
  -> assemble a bounded prompt using token-aware rolling compression
  -> classify intent with LLM + local semantic similarity + patterns
  -> retrieve knowledge for business intents
  -> build a TaskPlan for General, Technical, Billing, or AccountSecurity owners
  -> run scoped workers under one request deadline and max-Agent budget
  -> inside each worker, execute a bounded ReAct loop through allowlisted tools
  -> verify required-task coverage from typed task outcomes
  -> synthesize one candidate from SUCCESS / TIMEOUT / ERROR / BUDGET_EXCEEDED
  -> verify coverage, grounding, completeness, and safety (PASS / REJECT / UNKNOWN)
  -> feed PASS / REJECT quality back to the exact producing Agent instances
  -> publish only PASS answers; escalate every other outcome
  -> persist each escalation as one idempotent human-support ticket
  -> persist messages and update the profile in the background
  -> return TraceId, tool audit, and hybrid-memory retrieval evidence
```

Context input is bounded independently from model output. Working memory is
compressed from estimated token usage, not a fixed message count. The rolling
summary is structured and size-limited, recent turns stay verbatim, and an
optimistic Redis transaction prevents compression from dropping a concurrent
message. Long-term search stores raw episodic chunks and fuses vector, BM25,
and recency ranks with weighted reciprocal-rank fusion; the summary remains a
prompt projection rather than the only retrievable fact source. Retrieved knowledge and memory are tagged as data while actual
conversation history remains user/assistant messages.

See [docs/architecture.md](docs/architecture.md) for component ownership,
[docs/project-pitch.md](docs/project-pitch.md) for a concise technical walkthrough,
and [docs/full-architecture-tutorial.zh-CN.md](docs/full-architecture-tutorial.zh-CN.md)
for the complete Chinese repository tutorial, change history, failure analysis,
and project-specific interview follow-up guide.

## Technology

- Python 3.12, FastAPI, Pydantic, asyncio
- Anthropic-compatible chat API
- Redis working memory
- ChromaDB knowledge, episodic memory, and user profiles
- BM25 + weighted RRF hybrid long-term memory retrieval
- Bounded ReAct tool execution with allowlists, approval gates, and TraceId audit
- Prometheus monitoring and anomaly detection
- Docker Compose with Nginx, Redis, ChromaDB, and Prometheus
- Pytest and GitHub Actions

## Local development

Create configuration:

```bash
cp .env.example .env
```

Set at least:

```env
ANTHROPIC_API_KEY=your_key
```

Start the complete stack:

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

Swagger UI is available at `http://localhost:8000/docs`.

For a source-based development environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Redis and ChromaDB still need to be reachable using the values in `.env`.

## Primary endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Readiness and agent statistics |
| `POST` | `/chat` | Complete multi-agent conversation flow |
| `POST` | `/search` | Query rewrite, parallel retrieval, and reranking |
| `POST` | `/knowledge/add` | Add knowledge documents |
| `POST` | `/knowledge/upload` | Upload text, Markdown, or JSON knowledge |
| `GET` | `/skills` | Inspect loaded dynamic skills |
| `POST` | `/skills/reload` | Reload skills without a process restart |
| `GET` | `/monitor` | Agent/tool metrics, alerts, and suggestions |
| `POST` | `/eval/run` | Intent and end-to-end quality evaluation |
| `POST` | `/tickets` | Manually create an idempotent handoff ticket |
| `GET` | `/tickets` | List tickets by user and/or status |
| `GET` | `/tickets/{ticket_id}` | Read a ticket and its transition history |
| `PATCH` | `/tickets/{ticket_id}/status` | Apply a legal ticket status transition |

Example chat request:

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"client-request-001","user_id":"demo-user","message":"订单 #A123 登录失败后又被扣款了"}'
```

The response includes the selected intent and agents, structured task plan,
required-task coverage, execution budget, routing reason,
knowledge usage, typed verification status, groundedness, and escalation flag.
It also exposes `trace_id`, redacted `tool_audit`, and `memory_retrieval`
rank evidence. Each Agent outcome carries `react_status`, `react_steps`, and
`tool_call_ids` when tools were used.
Parallel responses also expose `synthesis_status`, conflict details, and each
selected task's typed execution outcome. Workers share one request deadline and
max-Agent budget; `BUDGET_EXCEEDED` remains attached to the unresolved task.
Coverage gaps, duplicate/unexpected outcomes, or detected conflicts trigger a
fail-closed handoff instead of being hidden by a fluent partial answer.

Runtime Agent statistics separate execution availability from verified answer
quality. `PASS` and `REJECT` update a sample-aware EWMA quality score for the
exact producer instances; verifier `UNKNOWN` is counted for observability but
does not penalize Agent quality. Routing combines availability, verified
quality, latency, and monitor penalties.
When escalation is required it also returns `ticket_id`, `ticket_status`, and
whether that request created the ticket or reused an idempotent existing one.

## Human-ticket lifecycle

SQLite is the authoritative ticket store. The supported lifecycle is:

```text
OPEN -> IN_PROGRESS -> WAITING_CUSTOMER -> IN_PROGRESS
                    \-> RESOLVED -> IN_PROGRESS
OPEN / IN_PROGRESS / WAITING_CUSTOMER / RESOLVED -> CLOSED
CLOSED -> terminal
```

Every successful transition appends an immutable event with actor, note, and
timestamp. Repeating the same status is an idempotent no-op; unsupported
transitions return HTTP `409`. A stable chat `request_id` guarantees that
network retries reuse the first handoff ticket.

## Verification contract

The answer verifier owns the publication decision:

- `pass`: publish the generated answer.
- `reject`: replace it with a safe handoff response and escalate.
- `unknown`: verifier failure or unsupported output; fail closed and escalate.

Malformed model output never becomes an implicit pass. Focused tests cover all
three states plus empty answers and model failures.

## Repository hygiene

Runtime databases, virtual environments, local secrets, logs, IDE settings,
and generated caches are intentionally excluded. Never commit `.env`.

## Current limitations

- Ticket endpoints currently have no authentication or role-based authorization;
  add an identity boundary before exposing them outside a trusted environment.
- SQLite is suitable for a single application writer; a multi-replica deployment
  should migrate the same TicketService contract to PostgreSQL.
- LLM verification adds latency and model cost to each published response.
- Built-in evaluation cases are suitable for regression checks, not production
  accuracy claims.
- No pre-generated quality baseline is committed; `/eval/run` creates one for
  the configured model and environment.
- Local development currently expects Redis and ChromaDB to be running.
- Trace spans and tool audits are process-local bounded memory, not durable
  OpenTelemetry storage; restarts remove them.
- Default approval safely blocks high-risk/write tools, but there is not yet an
  interactive approve-and-resume HTTP workflow.
- `MCPToolManager` is an internal tool runtime, not a remote MCP protocol server.
