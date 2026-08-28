# Architecture and ownership

DialogPilot is organized around authoritative boundaries rather than a single
prompt chain.

| Concern | Owner | Authoritative output |
|---|---|---|
| HTTP contract | `api/main.py` | Validated request/response models |
| Intent | `core/intent_recognizer.py` | Intent, confidence, urgency, entities |
| Agent selection | `agents/agent_orchestrator.py` | Structured routing decision |
| Tool reliability | `mcp/tool_manager.py` | Typed tool result and runtime statistics |
| Knowledge | `mcp/knowledge_base.py` | Retrieved ChromaDB documents |
| Conversation memory | `memory/conversation_memory.py` | Working, episodic, profile, and rolling-summary state |
| Prompt context | `memory/context.py` | Token estimation, typed sections, and bounded LLM input |
| Dynamic rules | `core/skill_loader.py` | Request-scoped skill prompt blocks |
| Publication safety | `services/answer_verifier.py` | PASS, REJECT, or UNKNOWN |
| Human handoff | `services/ticket_service.py` | Ticket identity, state, idempotency, and event history |
| Online health | `monitor/performance_monitor.py` | Alerts and routing penalties |
| Offline quality | `evaluation/evaluator.py` | Intent and response-quality reports |

## Temporal contract for `/chat`

1. Read memory before classifying the current request.
2. Classify once; reuse that result for knowledge selection and routing.
3. Retrieve knowledge only for supported business intents.
4. Execute one or more selected agents.
5. Verify the candidate answer before it crosses the response boundary.
6. If escalation is required, create or reuse one idempotent persistent ticket.
7. Persist only the answer that was actually published.
8. Update the user profile asynchronously after persistence.

This ordering prevents the memory store from claiming that an unverified model
answer was shown to the user.

## Context budget contract

`MemoryManager` owns persisted memory state. It triggers compression from an
estimated token budget rather than message count, replaces the prior summary
with a bounded structured rolling summary, and preserves the most recent raw
turn. The Redis rewrite uses an optimistic transaction: if messages arrive
while the summary model is running, the stale compression is discarded instead
of deleting the concurrent write.

`ContextAssembler` separately owns conversion into an LLM prompt. Memory,
retrieved knowledge, and profile data remain tagged data sections; real
user/assistant history remains real messages. It reserves output capacity,
trims oldest history, and never inserts fabricated assistant acknowledgements.

## Failure semantics

- Unknown intent with low confidence asks a clarification question.
- A failed specialist agent falls back to the general agent.
- Tool timeout, open circuit, or execution failure returns a controlled fallback.
- Verifier failure becomes `UNKNOWN`, never `PASS`.
- `REJECT` and `UNKNOWN` publish a deterministic handoff response and set
  `escalated=true`.
- Ticket persistence failure never claims a successful handoff; the response
  explicitly asks the client to retry with the same `request_id`.
- A compression model failure uses a bounded deterministic summary; a stale
  compression snapshot is not committed.

## Ticket state algebra

`TicketService` is the only owner allowed to change ticket state. Its bounded
state set is `OPEN`, `IN_PROGRESS`, `WAITING_CUSTOMER`, `RESOLVED`, and
`CLOSED`. Closed tickets are terminal; resolved tickets may be reopened to
`IN_PROGRESS`. Every accepted transition is written to `ticket_events` in the
same SQLite transaction as the ticket update. Same-state retries are no-ops.

The idempotency fingerprint covers the stable client operation—not generated
LLM wording—so a retry can safely reuse the first ticket even when model output
is nondeterministic.

## Extension points

- Add an Agent by defining its prompt and registering it in the orchestrator pool.
- Add a Tool by registering a typed `Tool` in the manager.
- Add business behavior with a `skills/<name>/SKILL.md` file.
- Replace model providers through the Anthropic-compatible configuration boundary.
- Replace SQLite with PostgreSQL behind the same TicketService contract for
  multi-replica writes.
