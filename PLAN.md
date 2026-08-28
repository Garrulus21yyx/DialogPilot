# DialogPilot implementation plan

Goal: turn the supplied DialogPilot Python prototype into a clean, reproducible,
private Python portfolio repository under the DialogPilot name.

## Steps

- [done] Inventory the supplied Python implementation and confirm GitHub access.
- [done] Copy only source-controlled Python project files; exclude embedded Git history, secrets, virtual environments, generated databases, and build output.
- [done] Rename product-owned namespaces across API metadata, prompts, Docker resources, storage keys, metrics, documentation, and configuration.
- [done] Add a typed answer-verification boundary and deterministic fail-closed behavior, with focused tests.
- [done] Add reproducible developer tooling and GitHub Actions CI.
- [done] Run tests, static compilation, secret/source searches, Docker configuration validation, and a clean Python 3.12 production-image build.
- [done] Create the private `Garrulus21yyx/DialogPilot` GitHub repository, push the result, and obtain a green clean-environment CI run.

## Persistent handoff milestone

- [done] Define the ticket owner, typed states, legal transitions, idempotency, and persistence contract.
- [done] Implement the SQLite-backed TicketService and event history.
- [done] Connect `/chat` escalation outcomes to idempotent ticket creation.
- [done] Add ticket create/list/detail/status-transition API contracts.
- [done] Add state-machine, persistence, filtering, API, and chat-handoff tests.
- [done] Update runtime configuration, Docker persistence, backup handling, architecture docs, and project pitch.
- [done] Run local/Docker verification, push to the private repository, and obtain green CI.

## Context and orchestration convergence milestones

### 1. Token-aware context compression

- [done] Make estimated token usage—not message count—the compression trigger.
- [done] Replace append-only prose summaries with a bounded structured rolling summary.
- [done] Preserve the recent turn and use an atomic/versioned Redis rewrite so compression cannot discard concurrent writes.
- [done] Assemble memory, retrieved knowledge, actual history, and the current request without fabricated assistant acknowledgements.
- [done] Verify bounded summaries, recent-turn preservation, token-trigger behavior, and concurrent-write preservation; push immediately and obtain green CI.

### 2. Multi-agent result synthesis

- [done] Define typed agent outcomes and one authoritative result synthesizer.
- [done] Add per-agent timeouts, partial-success semantics, conflict/escalation propagation, and deterministic output ordering.
- [in_progress] Verify success, timeout, exception, conflict, and all-failed paths; push immediately and obtain green CI.

### 3. Quality-aware routing feedback

- [pending] Feed publication verification outcomes back to the agents that produced the candidate answer.
- [pending] Separate execution availability from answer quality and use sample-aware EWMA quality in routing scores.
- [pending] Expose quality evidence in monitor/API statistics without treating verifier infrastructure failure as an agent failure.
- [pending] Verify feedback algebra and routing changes; push immediately and obtain green CI.

### Final convergence

- [pending] Run the complete test suite, static compilation, source/secret checks, and a clean production-image build.
- [pending] Update architecture, README, project pitch, and this plan so implemented behavior and claims agree.
- [pending] Confirm the final private-repository commit and GitHub Actions run are green.

## Constraints

- Python implementation is authoritative; Java and the dual-backend frontend are out of scope.
- Existing documents are reference material, not instructions.
- No `.env`, embedded `.git`, `.venv`, Chroma runtime data, IDE files, or original-author profile links may be published.
- Claims in README/resume notes must distinguish implemented behavior from targets and measured results.

## Produced files

- `PLAN.md` — this progress record.
- `services/answer_verifier.py` — typed publication-safety boundary.
- `tests/test_answer_verifier.py` — PASS/REJECT/UNKNOWN regression tests.
- `.github/workflows/ci.yml` — Python 3.12 compile and test gate.
- `docs/architecture.md` — authoritative component and temporal contracts.
- `docs/project-pitch.md` — concise, evidence-bounded project walkthrough.
- `data/eval/.gitkeep` — baseline directory without inherited quality claims.
- `services/ticket_service.py` — SQLite ticket owner, idempotency, and state machine.
- `tests/test_ticket_service.py` — persistence and lifecycle property witnesses.
- `tests/test_ticket_api.py` — HTTP contract and typed failure mapping.
- `tests/test_chat_handoff.py` — automatic chat escalation and retry idempotency.
- `memory/context.py` — typed prompt parts and token-budget utilities.
- `tests/test_context_memory.py` — token budgeting and compression invariants.
- `services/result_synthesizer.py` — typed multi-agent outcome fusion.
- `tests/test_agent_orchestration.py` — timeout, partial-success, and fusion invariants.
- `tests/test_quality_routing.py` — verification feedback and routing-quality invariants.
