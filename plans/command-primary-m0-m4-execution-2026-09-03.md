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
- [x] Keep planned invocation and observed use separate in the evaluation
  artifacts; defer any production trace type until a real runtime owner emits it.
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

Status: `IN_PROGRESS — READ-ONLY STICKY VERTICAL SLICE COMPLETE`

- [x] Split Turn State loading from optional ServiceEpisode retrieval.
- [x] Move current-thread state and active case before semantic routing.
- [x] Run deterministic resolution before the optional semantic producer.
- [x] Migrate Knowledge Authority/Execution compilation away from `route.intent`.
- [x] Use a post-decision compatibility projection on the Knowledge primary path.
- [x] Add the authoritative active-flow/pending-slot store before enabling sticky
  continuation in production.
- [x] Compile and commit a positive sticky read-only continuation through the
  existing tool, verification, publication, delivery, and Memory-write lifecycle.
- [x] Add the selective Encoder `ACCEPT/DEFER` → structured LLM command
  producer contract and its direct-evaluation adapter.
- [x] Add a pinned, local-only BGE-M3 provider with one truthful document/query
  embedding profile and no implicit hash fallback.
- [x] Wire independent Knowledge and ServiceEpisode provider selection into
  production composition; keep each rollout separately configurable.
- [x] Add an explicit ServiceEpisode canonical replay and immutable-generation
  activation owner; do not run it implicitly during application startup.
- [ ] Provision the pinned BGE-M3 artifact and rebuild/activate new immutable
  Knowledge and ServiceEpisode generations before benchmark runs.
- [ ] Replace the temporary selective legacy candidate adapter with the frozen
  Encoder → LLM command producer after its component gate passes.

### S4 — Evaluation runners and shadow gates

Status: `IN_PROGRESS — DIRECT RUNNERS + ONE REAL E2E SLICE COMPLETE`

- [x] Add the shared three-artifact eval schema and Understanding direct runner.
- [x] Add Knowledge, Memory, and Media direct adapters/runners.
- [x] Add invocation/consumption/state-transition assertions.
- [ ] Run component heldout only after lane-specific freeze.
- [x] Run one positive contract through the real `ChatApplication.handle()`.
- [ ] Seed and run the locked 80-case contract set; its current run status is
  deliberately `NOT_RUN`.
- [ ] Shadow and legacy-intent invariance gate before cutover.

## Produced files

- `research/command-primary-evaluation-v1/README.md`
- `research/command-primary-evaluation-v1/00-m0-m4-migration-master-plan.zh-CN.md`
- `research/command-primary-evaluation-v1/01-intent-understanding-architecture-and-evaluation.zh-CN.md`
- `research/command-primary-evaluation-v1/02-knowledge-rag-evaluation.zh-CN.md`
- `research/command-primary-evaluation-v1/03-memory-rag-evaluation.zh-CN.md`
- `research/command-primary-evaluation-v1/04-multimodal-evaluation.zh-CN.md`
- `research/command-primary-evaluation-v1/05-e2e-evaluation-and-scorecard.zh-CN.md`
- `data/eval/dialogpilot-synthetic-contract-v1/`

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
- 2026-09-03: S3 added one conversation-scoped FlowState aggregate with a
  two-operation port (`load`, `compare_and_set`) and PostgreSQL CAS. Active flow
  bindings now enter `TurnStateSnapshot` before semantic understanding. The
  persistence lifecycle is covered by round-trip, one-winner CAS, and
  conversation tombstone cleanup; complete isolated PostgreSQL suite:
  `985 passed`. No natural-language keyword matcher was added.
- 2026-09-03: S3 sticky positive slice loads a bound `refund_status` flow,
  lets the semantic command producer propose `CONTINUE_FLOW`, compiles the
  registered `refund.current_state` requirement and `refund_status` tool,
  executes exactly that read-only work, commits `ADVANCE` with CAS, then reuses
  verification, Publication, Delivery, and Memory write. Legacy Intent,
  Knowledge RAG, and Agent re-planning are skipped. Complete isolated
  PostgreSQL suite: `986 passed`. This proves transport/execution; it does not
  claim that the final Encoder/LLM producer has passed its quality gate.
- 2026-09-03: Current-thread Memory is confirmed as the bounded state read:
  production projection calls `get_current_context()`, and both direct and
  PostgreSQL paths return no ServiceEpisode hits. Cross-session retrieval stays
  behind its explicit authenticated ServiceEpisode capability; no additional
  production code was needed for this boundary.
- 2026-09-03: S4 introduced a thin Understanding direct runner with one shared
  `EvalCase`/manifest/prediction/report shape. It records Trigger, Artifact,
  Consumption, Outcome, and Cost, and writes exactly `manifest.json`,
  `predictions.jsonl`, and `report.json`. Focused suite: `1 passed`; `ruff`,
  `compileall`, and diff whitespace checks passed.
- 2026-09-03: Intent M3 added a selective command producer. Encoder output can
  bypass the LLM only when the versioned Registry resolves it to a low-risk,
  read-only action for which the current user command is sufficient; deferred
  and higher-risk proposals use the structured LLM producer. The S4 adapter
  records producer stage, command artifact, candidate consumption, status, and
  cascade cost. Focused suite: `9 passed`; no production cutover is claimed.
- 2026-09-03: Knowledge M3 added a 207-line local BGE-M3 provider adapter. It
  pins upstream revision and artifact digest, uses raw text for both document
  and query embeddings, exposes one 1024-dimensional MODEL profile, and never
  downloads or falls back to hash. Focused suite: `63 passed, 10 skipped`.
  Production injection and generation rebuild remain separate pending work.
- 2026-09-03: The core contract surface was simplified before further wiring.
  The unused standalone `CapabilityDecision`/`CapabilityTrace` prototype and
  unused `Observation` DTOs were removed. FlowState now exposes only the
  `PendingSlotRef` it actually owns; approval/resume remain with their existing
  Admission/ReAct owners. Focused suite: `13 passed, 2 skipped`; complete
  isolated PostgreSQL suite: `964 passed`.
- 2026-09-03: S4 direct component evaluation now has separate Knowledge,
  ServiceEpisode Memory, and routing-probe/Perception adapters. All reuse one
  188-line artifact runner instead of copying manifest/report logic. Knowledge
  reports evidence/document Recall, MRR, and nDCG; Memory reports Recall@K and
  MRR separately for reference-resolution and historical-evidence purposes;
  Media consumes an explicit tier request and never infers L1/L2 from message
  keywords. Focused component and adjacent suites: `60 passed`.
- 2026-09-03: Production composition now has one 72-line dense-provider
  factory with independent Knowledge and ServiceEpisode selection keys. Each
  defaults to its corpus-specific hash baseline; either can move to BGE-M3
  independently, while a joint rollout shares one local model instance.
  Configuration/load failure never falls back. Focused suite:
  `84 passed, 1 skipped`; focused real-PostgreSQL suite: `36 passed`.
- 2026-09-03: The 80-case synthetic architecture contract is now repository
  locked. Static validation reports `80 cases / 100 turns`, 40 counterfactual
  pairs, 159 resolved references, 18 matching file checksums, and `valid=true`.
  Its manifest remains honestly marked `run_status=NOT_RUN` and
  `promotion_allowed=false`. Dataset tests: `4 passed`.
- 2026-09-03: A thin E2E adapter now calls the real
  `ChatApplication.handle()` and reuses the shared artifact runner. The first
  sticky read-only contract verifies the visible result, FlowState CAS,
  component invocation/skips, and the exact read-only tool effect, while
  writing the standard three artifacts. Focused adjacent suite: `7 passed`.
- 2026-09-03: ServiceEpisode retrieval now exposes one explicit generation
  manager. It snapshots current canonical episode heads, reuses the production
  projector, validates the complete projection, builds the scoped HNSW index,
  and only then activates the new immutable generation. A real-PostgreSQL test
  proves the old hash generation remains truthfully labelled and becomes
  `RETIRED` while the new `MODEL` generation becomes `ACTIVE`. Focused and
  adjacent suite: `74 passed`.

## Commit log

- Stage 0 documentation/plan: `63fdf2f`.
- Stage 1 minimal command-primary slice: `d863261`.
- Stage 2 routing media probe: `b2a4334`.
- Stage 2 retrieval embedding ownership: `f83bddb`.
- Stage 3 Knowledge primary vertical slice: `07b42f8`.
- Stage 3 conversation FlowState owner: `61a8349`.
- Stage 3 sticky read-only vertical slice: `9e75275`.
- Stage 4 thin Understanding eval runner: `57bdb48`.
- Stage 3 selective command producer: `483066f`.
- Stage 3 pinned local BGE-M3 provider: `9f3e39e`.
- Stage 1 contract simplification: `1bcae69`.
- Stage 4 direct evidence runners: `a385fb4`.
- Stage 3 independent dense-provider rollout: `8ec08e1`.
- Stage 4 locked synthetic contract: `0706d41`.
- Stage 4 real-chat E2E slice: `e22839c`.
