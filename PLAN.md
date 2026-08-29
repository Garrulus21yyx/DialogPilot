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
- [done] Verify success, timeout, exception, conflict, and all-failed paths; push immediately and obtain green CI.

### 3. Quality-aware routing feedback

- [done] Feed publication verification outcomes back to the agents that produced the candidate answer.
- [done] Separate execution availability from answer quality and use sample-aware EWMA quality in routing scores.
- [done] Expose quality evidence in monitor/API statistics without treating verifier infrastructure failure as an agent failure.
- [done] Verify feedback algebra and routing changes; push immediately and obtain green CI.

### Final convergence

- [done] Run the complete test suite, static compilation, source/secret checks, and a clean production-image build.
- [done] Update architecture, README, project pitch, and this plan so implemented behavior and claims agree.
- [done] Confirm the final private-repository commit and GitHub Actions run are green.

## Complete architecture tutorial page

- [done] Locate the owner's local/private Agent Systems Atlas and extract only its project-question structure; leave that repository unchanged.
- [done] Trace every authoritative owner and the `/chat`, memory, retrieval, routing, synthesis, verification, ticket, monitoring, and evaluation paths from code.
- [done] Write one self-contained Chinese tutorial page with diagrams, data contracts, failure paths, configuration, tests, and the implemented change history.
- [done] Add interviewer follow-up questions with evidence-bounded model answers and explicit current limitations.
- [done] Run a fresh-reader comprehension review, repair gaps, link the page from README, test, push, and obtain green CI.

## Repository-wide Chinese commentary

Goal: make the Python repository readable to a Chinese-speaking maintainer without changing runtime behavior.

- [done] Inventory source, test, runtime configuration, deployment, and Page assets; define comment coverage and exclusions.
- [done] Add module/class/function docstrings and high-value inline comments to the Python runtime, preserving names, contracts, and control flow.
- [done] Add concise Chinese intent/invariant comments to tests so each acceptance gate explains what it proves.
- [done] Audit Docker, Compose, Nginx, Prometheus and shell scripts that already contain Chinese operational comments; annotate CI and Page assets where coverage was missing.
- [done] Run Python/JavaScript/Shell syntax checks, all 42 tests available at that milestone, whitespace checks, and a repository-wide Python docstring coverage audit.
- [done] Confirm the final CI and GitHub Pages runs remain green after the progress record is pushed (`33159826152`, `33159824710`).

Comment contract:

- Comments explain ownership, data flow, state transitions, concurrency, failure semantics, security boundaries, and design tradeoffs.
- Public behavior, identifiers, prompts, schemas, thresholds, and execution order remain unchanged.
- Obvious statements such as assignments and imports are not translated line by line.
- Generated/lock/runtime data, third-party code, and prose documents that are already Chinese are excluded.

Produced/updated commentary surfaces:

- Runtime: `api/`, `agents/`, `core/`, `memory/`, `mcp/`, `services/`, `monitor/`, `evaluation/`.
- Acceptance gates: the nine `tests/test_*.py` modules and 42 test functions present at that milestone.
- Delivery/Page: `.github/workflows/ci.yml`, `requirements-dev.txt`, `docs/_config.yml`, `docs/_includes/head.html`, `docs/index.md`, `docs/assets/dialogpilot.js`, and `docs/assets/main.scss`.
- Audited existing Chinese operational commentary: `Dockerfile`, `docker-compose.yml`, the three root shell scripts, `config/nginx/nginx.conf`, and `config/prometheus.yml`.

## Task-aware multi-agent convergence

- [done] Replace the Agent-list routing contract with TaskSpec/TaskPlan identity, scope, risk, Owner, and success criteria.
- [done] Add CoverageGate properties for missing, failed, duplicate, unexpected, and unresolved required tasks.
- [done] Add a dedicated AccountSecurityAgent and account-security Skill instead of routing security incidents to Billing.
- [done] Add a shared request deadline, per-Agent timeout, max-Agent limit, and typed BUDGET_EXCEEDED outcome.
- [done] Make AnswerVerifier deterministically reject incomplete coverage before model judgement and expose stable reason codes.
- [done] Add API projections and offline metrics for task coverage, exact Owner/task sets, budget success, and fan-out efficiency.
- [done] Update the Page with the Task Ledger, architecture diagrams, implementation evidence, and 50 project-specific interview drills.
- [done] Verify Python compilation, all 50 tests, JavaScript syntax, Markdown structure, and Page source consistency; push each implementation milestone.

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
- `docs/full-architecture-tutorial.zh-CN.md` — complete repository tutorial and interview follow-up guide.
- `tests/test_lifespan.py` — lifecycle wiring and memory-configuration ownership regression test.
- `tests/test_knowledge_context.py` — real evidence versus RAG fallback boundary tests.
