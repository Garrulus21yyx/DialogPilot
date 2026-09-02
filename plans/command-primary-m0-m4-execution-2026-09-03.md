# Command-primary M0–M4 implementation execution

Status: `IN_PROGRESS`
Started: 2026-09-03
Design source: `research/command-primary-evaluation-v1/`

## Goal

Migrate DialogPilot incrementally from intent-primary routing to a state-first, command-primary boundary, while preparing independently testable Knowledge, Memory, and Media production capabilities. Preserve current behavior until explicit shadow/cutover gates pass.

## Constraints

- Do not overwrite unrelated dirty-worktree changes.
- Make owner-level changes; evaluators must not manufacture missing production capabilities.
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

Status: `IN_PROGRESS`

- [x] Create target architecture and evaluation documents.
- [x] Confirm current unconditional intent ordering and downstream intent coupling.
- [ ] Record current focused test baseline for touched subsystems.

### S1 — M0/M1 shared contracts

Status: `IN_PROGRESS`

- [ ] Define command-primary types with closed state/outcome algebra.
- [ ] Define multi-mutation `FlowTransitionPlan` with positive invariants.
- [ ] Define `CapabilityDecision` separately from runtime `CapabilityTrace`.
- [ ] Add property/contract tests.

### S2 — Parallel production capability prerequisites

Status: `IN_PROGRESS`

- [ ] Knowledge raw-text Dense and truthful generation metadata.
- [ ] Memory ServiceEpisode Dense projection and purpose-specific policy.
- [ ] Routing-level media probe and bounded typed outcomes.

### S3 — State-first integration and downstream decoupling

Status: `PENDING`

- [ ] Split Turn State loading from optional ServiceEpisode retrieval.
- [ ] Move active state/case/pending binding before semantic routing.
- [ ] Integrate deterministic resolution before Encoder/LLM.
- [ ] Migrate Authority/Execution compilation away from `route.intent`.
- [ ] Keep legacy intent as post-decision projection only.

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

## Commit log

- Stage 0 documentation/plan: recorded by the commit containing this plan revision.
