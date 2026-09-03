# Command-primary M0–M4 implementation execution

Status: `IN_PROGRESS`
Started: 2026-09-03
Design source: `research/command-primary-evaluation-v1/`

## Goal

Migrate DialogPilot incrementally from intent-primary routing to a state-first, command-primary boundary, while preparing independently testable Knowledge, Memory, and Media production capabilities. Preserve current behavior until explicit shadow/cutover gates pass.

## Constraints

- Do not overwrite unrelated dirty-worktree changes.
- Make owner-level changes; evaluators must not manufacture missing production capabilities.
- Prefer small positive interfaces and one executable vertical path over defensive
  fields, counterexample-specific branches, or an all-purpose contract module.
- Keep legacy intent behavior as baseline/compatibility until command-primary invariants pass.
- No model downloads or final benchmark claims during M0/M1 implementation.
- All new contracts require typed failure behavior and focused tests.
- After each verified stage, commit only that stage's explicit file set and push it immediately; never absorb unrelated dirty-worktree changes.

## Parallel ownership

| Lane | Owner | Initial bounded deliverable | Shared files excluded |
|---|---|---|---|
| Core/Intent | root | M0/M1 command-primary contracts and invariants; integration plan | Knowledge/Memory/Media implementation files |
| Knowledge | sub-agent | Raw-text Dense input + embedding/generation metadata owner and tests | `chat_application.py`, core command contracts |
| Memory | sub-agent | ServiceEpisode Dense projection/query contract and purpose-specific policy boundary/tests | `chat_application.py`, core command contracts |
| Media | sub-agent | RoutingMediaProbe contract/producer and tests | `chat_application.py`, core command contracts |

## Steps

### S0 — Baseline and ownership audit

Status: `COMPLETED`

- [x] Create target architecture and evaluation documents.
- [x] Confirm current unconditional intent ordering and downstream intent coupling.
- [x] Record current focused test baseline for touched subsystems.

### S1 — M0/M1 shared contracts

Status: `IMPLEMENTED_FOR_VERTICAL_INTEGRATION`

- [x] Define command-primary types with closed state/outcome algebra.
- [x] Define the minimal `FlowTransitionPlan` needed by the supported command path.
- [x] Define `CapabilityDecision` separately from runtime `CapabilityTrace`.
- [x] Prove a positive vertical slice: state-first resolution or semantic defer,
  registry-owned policy, then deterministic turn-plan compilation.

This status does not claim that the production chain has cut over. It means the
shared boundary is ready to be wired into the production chain in S3. The S1
implementation is split by responsibility; no command-primary module exceeds
330 lines. New validation is added only when a supported production invariant
requires it, not by accumulating hypothetical counterexamples.

### S2 — Parallel production capability prerequisites

Status: `COMPLETED`

- [x] Knowledge raw-text Dense and truthful generation metadata.
- [x] Memory ServiceEpisode Dense projection and purpose-specific policy.
- [x] Routing-level media probe and bounded typed outcomes.

S2 closes provider/profile plumbing and independently testable component paths.
The API deliberately labels its current local providers as `HASH_BASELINE`.
Replacing those providers with pinned BGE-M3 and rebuilding active generations
remains an M3 production capability task; S2 does not claim that score.

### S3 — State-first integration and downstream decoupling

Status: `IN_PROGRESS — KNOWLEDGE VERTICAL SLICE COMPLETE`

- [ ] Split Turn State loading from optional ServiceEpisode retrieval.
- [x] Move current-thread state and active case before semantic routing.
- [x] Run deterministic resolution before the optional semantic producer.
- [x] Migrate Knowledge Authority/Execution compilation away from `route.intent`.
- [x] Use a post-decision compatibility projection on the Knowledge primary path.
- [ ] Add the authoritative active-flow/pending-slot store before enabling sticky
  continuation in production.
- [ ] Replace the temporary selective legacy candidate adapter with the frozen
  Encoder → LLM command producer after its component gate passes.

### S4 — Evaluation runners and shadow gates

Status: `PENDING`

- [ ] Add shared eval schema and component adapters/runners.
- [ ] Add invocation/consumption/state-transition assertions.
- [ ] Run component heldout only after lane-specific freeze.
- [ ] Run real `ChatApplication.handle()` contract E2E.
- [ ] Shadow and legacy-intent invariance gate before cutover.

## Produced files

- `research/command-primary-evaluation-v1/README.md`
- `research/command-primary-evaluation-v1/00-m0-m4-migration-master-plan.zh-CN.md`
- `research/command-primary-evaluation-v1/01-intent-understanding-architecture-and-evaluation.zh-CN.md`
- `research/command-primary-evaluation-v1/02-knowledge-rag-evaluation.zh-CN.md`
- `research/command-primary-evaluation-v1/03-memory-rag-evaluation.zh-CN.md`
- `research/command-primary-evaluation-v1/04-multimodal-evaluation.zh-CN.md`
- `research/command-primary-evaluation-v1/05-e2e-evaluation-and-scorecard.zh-CN.md`

## Verification log

- 2026-09-03: Research-document relative links and fenced blocks validated.
- 2026-09-03: S1 focused vertical suite: `43 passed`.
- 2026-09-03: Full non-database suite: `850 passed, 141 skipped`; two existing
  stateful-runner tests require `TEST_DATABASE_URL`/`DATABASE_URL` for the
  `ticket_idempotent` fixture and are recorded as environment-blocked, not
  converted into product skips.
- 2026-09-03: `ruff`, `compileall`, and diff whitespace checks passed for the
  focused S1 code/test files.
- 2026-09-03: S2 Media Probe reduced to a 165-line asset binding/reuse
  component; it consumes an explicit routing media need instead of classifying
  natural language with keyword lists. Focused suite: `7 passed`; `ruff`
  passed.
- 2026-09-03: S2 Knowledge/Memory focused real-PostgreSQL suite: `117 passed`.
  Complete repository suite with isolated PostgreSQL databases: `984 passed`.
  Knowledge raw chunks and queries now share one explicit embedding profile;
  ServiceEpisode projection and query use the same explicit provider, with
  separate reference-resolution and historical-evidence policies.
- 2026-09-03: S3 Knowledge command-primary path runs through the real
  `ChatApplication.handle()` lifecycle. Current-thread state and active case are
  read before understanding; a native command producer skips legacy Intent and
  Agent execution, then reuses Knowledge retrieval, verification, Publication,
  Delivery, and Memory projection. The optional production migration adapter is
  still explicitly legacy-backed until the Encoder/LLM command producer is
  frozen. Focused suite: `78 passed`; complete isolated PostgreSQL suite:
  `983 passed`.

## Commit log

- Stage 0 documentation/plan: `63fdf2f`.
- Stage 1 minimal command-primary slice: `d863261`.
- Stage 2 routing media probe: `b2a4334`.
- Stage 2 retrieval embedding ownership: `f83bddb`.
