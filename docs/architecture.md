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
| Conversation context | `memory/conversation_memory.py` | Working, episodic, and profile context |
| Dynamic rules | `core/skill_loader.py` | Request-scoped skill prompt blocks |
| Publication safety | `services/answer_verifier.py` | PASS, REJECT, or UNKNOWN |
| Online health | `monitor/performance_monitor.py` | Alerts and routing penalties |
| Offline quality | `evaluation/evaluator.py` | Intent and response-quality reports |

## Temporal contract for `/chat`

1. Read memory before classifying the current request.
2. Classify once; reuse that result for knowledge selection and routing.
3. Retrieve knowledge only for supported business intents.
4. Execute one or more selected agents.
5. Verify the candidate answer before it crosses the response boundary.
6. Persist only the answer that was actually published.
7. Update the user profile asynchronously after persistence.

This ordering prevents the memory store from claiming that an unverified model
answer was shown to the user.

## Failure semantics

- Unknown intent with low confidence asks a clarification question.
- A failed specialist agent falls back to the general agent.
- Tool timeout, open circuit, or execution failure returns a controlled fallback.
- Verifier failure becomes `UNKNOWN`, never `PASS`.
- `REJECT` and `UNKNOWN` publish a deterministic handoff response and set
  `escalated=true`.

## Extension points

- Add an Agent by defining its prompt and registering it in the orchestrator pool.
- Add a Tool by registering a typed `Tool` in the manager.
- Add business behavior with a `skills/<name>/SKILL.md` file.
- Replace model providers through the Anthropic-compatible configuration boundary.
- Implement a persistent ticket service as the consumer of escalation outcomes.
