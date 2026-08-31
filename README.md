# DialogPilot

DialogPilot is an asynchronous multi-agent customer-support backend built with
Python and FastAPI. It combines intent recognition, retrieval-augmented
generation, dynamic business skills, layered conversation memory, observable
agent routing, and a fail-closed answer-verification boundary.

Knowledge ingestion uses a configurable 360-token estimate ceiling with a
48-token overlap. Stable chunk IDs remain authoritative through vector recall,
BM25, RRF, and evidence projection; parent document IDs are deduplicated only
after ranking. Existing v1 Chroma chunks remain readable but require a source
reindex to gain the v2 overlap policy; ingestion never silently rewrites them.

Chinese documentation: [full architecture tutorial](https://garrulus21yyx.github.io/DialogPilot/) · [code-checked interview guide](https://garrulus21yyx.github.io/DialogPilot/interview-guide.html) · [500-case layered evaluation](https://garrulus21yyx.github.io/DialogPilot/evaluation-500/)

## Why this project exists

A single-prompt chatbot mixes routing, knowledge access, memory, and response
generation into one opaque operation. DialogPilot gives each concern a clear
owner and exposes the routing and verification decisions in the API response.

## Request flow

```text
POST /chat
  -> normalize and screen high-confidence direct prompt-injection attempts before any model or memory access
  -> load uncovered Redis events, range summaries, sourced facts, and hybrid episodic memory
  -> assemble a bounded prompt from those projections
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
  -> persist verifier, coverage, and uncertain tool-effect failures as deduplicated Bad Case candidates
  -> append the published turn with contiguous conversation-local sequence numbers
  -> extract source-linked, versioned facts in the background
  -> explicitly finalize short sessions by advancing their summary checkpoint
  -> return redacted TraceId, tool audit, and hybrid-memory retrieval evidence
```

Context input is bounded independently from model output. Every conversation turn
is first retained as an append-only raw event with a monotonic `seq`. Compression
creates immutable, structured chunks for explicit sequence ranges and advances a
checkpoint with optimistic CAS; it never rewrites or deletes the raw event log.
Newer messages therefore do not invalidate a completed older-range summary.
Long-term search stores raw episodic chunks and fuses vector, BM25, and recency
ranks with weighted reciprocal-rank fusion. User memory is stored as typed facts
with source message IDs and active/superseded/retracted lifecycle, not one mutable
profile blob. Retrieved knowledge and memory are tagged as data while actual
conversation history remains user/assistant messages.

The production registry exposes nine bounded tools: knowledge, user memory,
ticket list/detail/create, order lookup, refund eligibility, refund-request
creation, and account-security events. Write tools require host approval and
return typed SQLite receipts. A refund receipt proves that the local request was
committed; it does not claim that an external payment rail moved money.

文档入口：[docs/architecture.md](docs/architecture.md) 说明组件职责归属，
[docs/project-pitch.md](docs/project-pitch.md) 提供中文项目讲述与技术取舍，
[docs/full-architecture-tutorial.zh-CN.md](docs/full-architecture-tutorial.zh-CN.md)
提供完整仓库教程、改造历史、故障分析和项目追问指南。
The reproducible Flash/off vs Flash/high vs Pro/high pilot, including latency,
usage, cost, and failure cases, is in
[docs/model-ablation-report.zh-CN.md](docs/model-ablation-report.zh-CN.md).

## Technology

- Python 3.12, FastAPI, Pydantic, asyncio
- Anthropic-compatible chat API
- Role-tiered DeepSeek Flash/Pro profiles with explicit reasoning policy
- Redis append-only conversation events, summary chunks, and checkpoints
- ChromaDB knowledge, episodic memory, and source-linked user facts
- BM25 + weighted RRF hybrid long-term memory retrieval
- Bounded ReAct tool execution with allowlists, approval gates, and TraceId audit
- Nine production Agent tools spanning knowledge, memory, tickets, orders, refund requests, and security events
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

The checked-in example selects DeepSeek's Anthropic-compatible endpoint and
uses Flash without thinking for intent, workers, ReAct, memory, rewrite, and
rerank. Cross-domain synthesis, answer verification, and the offline judge use
Pro with thinking disabled by default. A live three-case verifier pilot found
Flash/none and Pro/none both parsed 3/3 cases, while Pro/high was roughly three
times slower and had previously exhausted short completion budgets. Every role can be overridden independently with
`MODEL_<ROLE>` and `MODEL_<ROLE>_REASONING`; `/health` and evaluation metadata
record the effective, non-secret policy. See `.env.example` for the complete
matrix. Reasoning profiles enforce a minimum completion budget so thinking
cannot silently consume the entire response before structured JSON is emitted.

Start the complete stack:

```bash
docker compose up -d --build
curl http://localhost:18000/health
```

Swagger UI is available at `http://localhost:18000/docs`.

For a source-based development environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Redis must be reachable. Chroma uses the explicit `CHROMA_MODE`: `remote`
fails startup when the declared server is unavailable; `embedded` uses only
`CHROMA_PERSIST_DIRECTORY` and never silently switches to the remote store.

## Primary endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Readiness and agent statistics |
| `POST` | `/chat` | Complete multi-agent conversation flow |
| `POST` | `/conversations/{conv_id}/finalize` | Idempotently archive a short session before clearing Redis |
| `POST` | `/search` | Query rewrite, parallel retrieval, and reranking |
| `POST` | `/knowledge/add` | Add knowledge documents |
| `POST` | `/knowledge/upload` | Upload text, Markdown, or JSON knowledge |
| `GET` | `/skills` | Inspect loaded dynamic skills |
| `POST` | `/skills/reload` | Reload skills without a process restart |
| `GET` | `/monitor` | Agent/tool metrics, alerts, and suggestions |
| `GET` | `/eval/datasets` | List versioned evaluation sets and review state |
| `POST` | `/eval/run` | Run selected intent/routing dataset slices or smoke cases |
| `POST` | `/tickets` | Manually create an idempotent handoff ticket |
| `GET` | `/tickets` | List tickets by user and/or status |
| `GET` | `/tickets/{ticket_id}` | Read a ticket and its transition history |
| `PATCH` | `/tickets/{ticket_id}/status` | Apply a legal ticket status transition |
| `POST` | `/feedback` | Submit authenticated negative feedback as a provisional Bad Case candidate |
| `GET` | `/bad-cases` | Admin queue filtered by lifecycle, stage, and severity |
| `GET` | `/bad-cases/{badcase_id}` | Read a Bad Case and immutable transition audit |
| `PATCH` | `/bad-cases/{badcase_id}/status` | Apply an evidence-gated Bad Case transition |

Example chat request:

```bash
# Create a development token. Use a different long secret outside this example.
export AUTH_JWT_SECRET='replace-with-at-least-32-random-bytes'
export DIALOGPILOT_TOKEN="$(python - <<'PY'
import os, time, jwt
now = int(time.time())
print(jwt.encode({
    "sub": "demo-user", "scope": "chat", "iat": now, "exp": now + 3600,
    "iss": "dialogpilot", "aud": "dialogpilot-api",
}, os.environ["AUTH_JWT_SECRET"], algorithm="HS256"))
PY
)"

curl -X POST http://localhost:18000/chat \
  -H "Authorization: Bearer $DIALOGPILOT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"client-request-001","message":"订单 #A123 登录失败后又被扣款了"}'
```

The response includes the selected intent and agents, structured task plan,
required-task coverage, execution budget, routing reason,
knowledge usage, typed verification status, groundedness, and escalation flag.
It also exposes `trace_id`, redacted `tool_audit`, and `memory_retrieval`
rank evidence. Public Agent outcomes retain status and timing diagnostics but
remove candidate content, raw internal errors, producer keys, and tool call IDs.
Parallel responses also expose `synthesis_status`, conflict details, and each
selected task's typed execution outcome. Workers share one request deadline and
max-Agent budget; `BUDGET_EXCEEDED` remains attached to the unresolved task.
Coverage gaps, duplicate/unexpected outcomes, or detected conflicts trigger a
fail-closed handoff instead of being hidden by a fluent partial answer.

## Versioned evaluation data

The committed `dialogpilot-500-v1` suite contains 500 cases: 180 intent/OOS,
120 TaskPlan routing, 100 retrieval, and 100 stateful memory/tool-safety cases,
with a group-safe 400/100 dev/heldout split. External intent cases remain
`auto_mapped`; project cases remain `provisional`, so the default scorer still
excludes them from project-gold metrics until human review.

```bash
# Validate schema, checksums, references, and group-safe dev/heldout splits.
python -m evaluation.dataset data/eval/dialogpilot-500-v1

# Execute real repository-owner fixtures for the stateful layer.
python -m evaluation.stateful_runner data/eval/dialogpilot-500-v1 \
  --split dev --predictions artifacts/eval/stateful-dev-predictions.jsonl \
  --report artifacts/eval/stateful-dev-report.json

# After inspecting/correcting selected case inputs and expected labels:
python scripts/review_eval_dataset.py data/eval/dialogpilot-500-v1 \
  --case-id intent-dev-negation-01 \
  --reviewer reviewer-a --notes 'intent and ambiguity checked' \
  --confirm-human-review

# Build external pressure-test data (generated output is gitignored and non-gold).
python scripts/build_eval_dataset.py --source banking77 --max-per-label 20
python scripts/build_eval_dataset.py --source clinc150-oos --max-per-label 20

# Score a complete prediction JSONL for one split. Default: human-reviewed only.
python -m evaluation.benchmark data/eval/dialogpilot-500-v1 predictions.jsonl --split heldout

# Dry-run provisional/auto-mapped cases; do not publish this as project accuracy.
python -m evaluation.benchmark data/eval/dialogpilot-500-v1 predictions.jsonl \
  --split heldout --include-non-gold

# Export only a reproduced/fixed case into a versioned dev regression bundle.
# The output stays provisional; this command cannot create Gold or heldout.
python scripts/promote_badcase.py \
  --database ./data/badcases/badcases.db \
  --badcase-id BADCASE_ID \
  --output ./data/eval/dialogpilot-badcase-regression-v1 \
  --actor reviewer-a
```

BANKING77 contributes overlapping customer-support intents and CLINC150
contributes out-of-scope examples. Their original label, upstream split,
license, URL, and mapping version remain in every generated case. Bitext is
opt-in because its CDLA-Sharing-1.0 obligations must be accepted explicitly:

```bash
python scripts/build_eval_dataset.py --source bitext --max-per-label 20 \
  --accept-cdla-sharing
```

With an admin token, `GET /eval/datasets` exposes counts and review status.
`POST /eval/run` accepts `dataset_id`, `split`, `layers`, and
`include_non_gold`; intent/routing execute through the live runtime. Stateful
memory/security cases execute through isolated real-owner fixtures and the
deterministic scorer. Retrieval uses its isolated Chroma prediction producer;
an unsupported live layer cannot be silently reported as run.
Every runtime report carries dataset version, checksum, split, layer set, and
review scope.

Reviewer B's 27-case fresh-v2 specification is fully registered and executes
the context, memory, RAG, tool, verifier, coverage and publication owners. It
passes 27/27 as a **consumed regression set**, not as an unseen holdout. Fixture
producers receive an immutable `FixtureRequest` without `expected`; write-tool
timeout/cancellation use explicit terminal audit states and report
`outcome_unknown` whenever the runtime cannot prove the business commit.

Runtime Agent statistics separate execution availability from verified answer
quality. `PASS` and `REJECT` update a sample-aware EWMA quality score for the
exact producer instances; verifier `UNKNOWN` is counted for observability but
does not penalize Agent quality. Routing combines availability, verified
quality, latency, and monitor penalties. `adaptive_routing_active` is true only
when a type has at least two instances that can actually replace one another.
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

## Bad Case quality loop

`BadCaseRegistry` separately owns production-quality incidents. Verifier
REJECT/UNKNOWN, incomplete required-task coverage, and failed or uncertain tool
effects are captured without blocking the current response. `/feedback` adds
authenticated user reports. Inputs and evidence are bounded and redacted,
users are stored as keyed HMAC pseudonyms, and unpublished candidates are never copied.

The lifecycle is `candidate -> triaged -> reproduced -> fixing ->
regression_pass -> verified -> closed`. Reproduction requires an Owner fixture,
assertion list, and evidence SHA-256; a fixed commit is required before a
regression can pass. Recurrence reopens a closed record. Exported cases always
use the existing intent/routing/retrieval/stateful layers with `split=dev`,
`status=provisional`, and `consumed_regression`; human Gold remains a separate,
explicit review action.

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

- HTTP routes verify HS256 bearer tokens; chat memory identity comes from the
  signed `sub`, while admin and knowledge routes require scopes. Multi-tenant
  organization policy and external IdP/JWKS integration remain future work.
- SQLite is suitable for a single application writer; a multi-replica deployment
  should migrate the TicketService and BadCaseRegistry contracts to PostgreSQL.
- LLM verification adds latency and model cost to each published response.
- The repository has a 500-case provisional layered suite but no human-reviewed
  gold cases yet. Stateful heldout has been consumed as regression evidence;
  none of these results supports a production accuracy claim.
- No pre-generated quality baseline is committed; `/eval/run` creates one for
  the configured model and environment.
- Local development expects Redis; Chroma must be explicitly `remote` or `embedded`.
- Trace spans and tool audits are process-local bounded memory, not durable
  OpenTelemetry storage; restarts remove them.
- Default approval safely blocks high-risk/write tools, but there is not yet an
  interactive approve-and-resume HTTP workflow.
- `MCPToolManager` is an internal tool runtime, not a remote MCP protocol server.
