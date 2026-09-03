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
- [x] Add a strict Registry-backed LLM command producer and the existing
  observed Anthropic transport; keep default production wiring unchanged until
  the semantic quality gate passes.
- [x] Add an explicit LLM-only correctness mode whose Encoder always defers;
  prove one real Knowledge request without invoking legacy Intent while the
  default runtime mode remains off.
- [x] Add a pinned, local-only BGE-M3 provider with one truthful document/query
  embedding profile and no implicit hash fallback.
- [x] Verify the configured local BGE-M3 weight artifact against its declared
  SHA-256 before loading it.
- [x] Wire independent Knowledge and ServiceEpisode provider selection into
  production composition; keep each rollout separately configurable.
- [x] Add an explicit ServiceEpisode canonical replay and immutable-generation
  activation owner; do not run it implicitly during application startup.
- [x] Add one explicit pre-evaluation CLI that rebuilds only the selected
  Knowledge and/or ServiceEpisode generation and reports the pointer change.
- [x] Provision and verify the pinned local BGE-M3 artifact, then rebuild and
  activate the development Knowledge generation through the explicit CLI.
- [x] Compile a task-owned, current-turn single-asset L1 media requirement,
  execute OCR, consume its artifact, and publish through the real chat lifecycle
  without legacy Intent, message-keyword tier selection, or business tools.
- [x] Add the authenticated Case Owner fact and projection chain: accept one
  resolution/outcome, bind its real invocation turns, atomically resolve the
  Ticket, compile the canonical ServiceEpisode, rebuild its generation, and
  retrieve it for the same subject.
- [x] Expose accepted resolution through an authenticated Case Owner HTTP
  boundary and make the explicit generation CLI drain all pending Episode
  projections before rebuilding; keep it out of application startup.
- [ ] Seed and freeze a non-empty lifecycle-backed ServiceEpisode evaluation
  corpus before reporting production Memory scores. The current database and
  API smokes prove transport, not retrieval quality.
- [ ] Replace the temporary selective legacy candidate adapter with the frozen
  Encoder → LLM command producer after its component gate passes.

### S4 — Evaluation runners and shadow gates

Status: `IN_PROGRESS — DIRECT RUNNERS + ONE REAL E2E SLICE COMPLETE`

- [x] Add the shared three-artifact eval schema and Understanding direct runner.
- [x] Add Knowledge, Memory, and Media direct adapters/runners.
- [x] Add a benchmark-only LoCoMo session retrieval adapter and baseline without
  writing public conversations into the production ServiceEpisode owner.
- [x] Run token, pinned BGE-M3 cosine, and fixed lexical+dense RRF over the
  70-case LoCoMo session slice as `DEV_DIAGNOSTIC`, not as production Memory
  policy selection.
- [x] Add invocation/consumption/state-transition assertions.
- [x] Add a dedicated PostgreSQL raw-query candidate runner that records the
  real BGE-M3 generation and explicitly excludes rewrite, rerank, parent
  expansion, packing, generation, and judge stages.
- [x] Run the fixed Knowledge component baseline once on a mechanically frozen,
  conversation-isolated English Doc2Dial heldout after the Dev configuration
  freeze; keep Memory and official Media heldout separately not run.
- [x] Run all four predeclared Knowledge chunk profiles on the already-viewed
  Doc2Dial heldout only as `DIAGNOSTIC_ONLY`; do not use those results to choose
  a configuration.
- [x] Select the Knowledge chunk profile on the designated Doc2Dial Dev using the
  predeclared All-evidence → Evidence Recall ordering.
- [x] Capture lexical/dense Top-40 once for the selected `fixed-512-64` profile,
  replay the predeclared fusion grid offline, and freeze one candidate policy.
- [x] Reuse the bound 48-case Dev standalone artifact, capture fresh Raw and
  Standalone Dense Top-40 on the frozen chunk/fusion path, replay four query
  weights offline, and freeze the production-fallback-compatible query candidate.
- [x] Replay the frozen Query candidates through identity selection and the
  production ContextPacker for `K=3/5/8 × budget=1800/2600`, then freeze the
  viewed-Dev no-rerank packing baseline without changing production defaults.
- [x] Run one positive contract through the real `ChatApplication.handle()`.
- [x] Prove the existing L1 media transport through the real chat lifecycle;
  keep it labelled as transport evidence, not command-primary media closure.
- [x] Run one locked, explicit-region L1 fixture with the real local Tesseract
  provider and record page/bbox/crop grounding plus consumption as
  `PROJECT_DIAGNOSTIC`.
- [ ] Seed and run the locked 80-case contract set; its current run status is
  deliberately `NOT_RUN`.
- [x] Run one locked L0 input through the real clarification publication path
  as a non-scoring `TRANSPORT_SMOKE`; it remains ineligible for `x/80` until a
  frozen semantic producer replaces the test stub.
- [x] Run the 20-case locked L0 clarification slice with the live structured
  provider as `DEV_CONTRACT_DIAGNOSTIC`; after one generic status-semantics
  repair the progression is `2/20 → 11/20 → 13/20`. Stop tuning this viewed
  slice and keep command-primary promotion blocked pending fresh heldout.
- [x] Keep `NO_SUPPORTED_FLOW → OUT_OF_SCOPE` on the command-primary policy
  terminal path, with legacy Intent and Agent execution skipped.
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
- 2026-09-03: The BGE-M3 provider now hashes the supported local weight file
  before model construction and fails closed on a missing or mismatched
  artifact. The available pinned snapshot (`5617a9f…`) was loaded fully
  offline and produced normalized 1024-dimensional Chinese and English query
  embeddings. Focused suite: `49 passed`.
- 2026-09-03: A current-media transport test now runs an explicit L1 decision
  through `ChatApplication.handle()`, the real `TieredPerceptionService`, an OCR
  artifact, worker consumption, verification, Publication, Delivery, and
  Memory write. The answer consumes and publishes `E401`; OCR is invoked once,
  VLM and business tools zero times. This does not claim that the routing probe
  or task-conditioned media policy has moved to command-primary ownership.
  Focused adjacent suite: `31 passed, 4 skipped`.
- 2026-09-03: A thin offline CLI now composes the existing Knowledge store and
  ServiceEpisode generation manager, runs only the explicitly selected corpus,
  and reports old/new immutable identities, complete embedding profiles, and
  indexed counts as JSON. It does not migrate schemas, download models, or run
  during application startup. CLI unit test: `1 passed`; adjacent real-
  PostgreSQL suite: `19 passed`.
- 2026-09-03: The explicit CLI activated development Knowledge generation
  `knowledge-generation-5c2770ace16dde4211f11c4b62852d46` with the verified
  1024-dimensional BGE-M3 profile and 6 projected chunks. A real PostgreSQL
  candidate smoke placed the refund policy Top-1 for a Chinese refund query and
  delivery guidance Top-1 for an English delivery query. This is transport and
  retrieval evidence, not a heldout score.
- 2026-09-03: Optional Knowledge product scope now has one retrieval-side
  representation: blank input is canonicalized to `None` (no product filter).
  This closed the mismatch between canonical manifest `''` and projected search
  `NULL`; focused real-PostgreSQL suite: `34 passed`.
- 2026-09-03: The first task-owned command-primary media path now compiles L1
  from the versioned Action definition, binds one current-turn asset, runs OCR,
  consumes the resulting artifact, and publishes `E401` through
  `ChatApplication.handle()`. Legacy Intent, Knowledge, and business tools are
  skipped. Focused root recheck: `54 passed`; isolated-PostgreSQL full suite:
  `983 passed`. Supported scope remains one current-turn asset at L1; L2,
  region selection, cross-turn reuse, and joint Media+Knowledge are still open.
- 2026-09-03: A dedicated eval database and the pinned local BGE-M3 provider
  ran the Doc2Dial heldout candidate smoke: 40 documents became 292 chunks and
  all 48 raw queries completed without system failure. For
  `structure-aware 256/32`, candidate@20 Evidence Recall was `.4896`, Document
  Recall `.6042`, All-evidence Recall `23/48=.4792`, MRR `.2718`, nDCG `.3249`,
  and retrieval P95 `140.64ms`. This is one raw-only baseline, not a four-profile
  selection result.
- 2026-09-03: Command-primary CLARIFY now uses the existing terminal execution
  algebra and publication lifecycle. One locked L0 input ran through the real
  chat handler with Intent, Knowledge, Media, Agent, and tools skipped. Because
  the semantic producer is a fixed test stub, the adapter records
  `TRANSPORT_SMOKE` and `score_eligible=false`; the locked manifest remains
  `NOT_RUN`. Focused suite: `57 passed`; full isolated-PostgreSQL suite:
  `986 passed`.
- 2026-09-03: The structured LLM producer now renders only action-backed
  Registry commands, accepts strict whole-object JSON, and returns typed
  provider versus invalid-output outcomes. Risk, requirements, authority, and
  tools remain absent from its input/output authority. Adjacent suite:
  `13 passed`; Ruff, formatting, compile, and diff checks passed.
- 2026-09-03: `structured_knowledge_primary` now composes an explicitly
  uncalibrated always-defer Encoder with the structured LLM producer. A real
  `ChatApplication.handle()` FAQ request invoked the provider once, invoked
  legacy Intent zero times, and completed Registry compilation, Knowledge,
  verification, Publication, Delivery, and Memory write. Default mode remains
  `off`; adjacent root suite: `30 passed`.
- 2026-09-03: A controlled heldout diagnostic ran all four predeclared chunk
  profiles with one BGE-M3 profile and candidate policy. Structure 384/48 led
  Evidence/Document Recall; fixed 512/64 led MRR/nDCG. Because the heldout was
  observed and the metrics disagree, all four results are
  `DIAGNOSTIC_ONLY`; selection has moved to the designated Doc2Dial Dev split.
- 2026-09-03: The designated Doc2Dial Dev selection ran all four profiles over
  300 cases with zero system failures. `fixed-512-64` won the predeclared first
  criterion with All-evidence `140/300=.4667` and also led Evidence Recall
  (`.4700`), MRR (`.3064`), and nDCG (`.3470`). It is frozen only for the next
  fusion experiment, not deployed. Its one-run P95 (`564.18ms`) is treated as
  anomalous and must be remeasured before any SLO claim.
- 2026-09-03: The Memory benchmark audit separated public conversation-session
  retrieval from production ServiceEpisode semantics. LoCoMo and LongMemEval
  do not contain resolved cases with accepted authoritative outcomes and must
  not be written to the ServiceEpisode owner. LongMemEval oracle is reserved
  for reader/consumption because its answer sessions are already supplied;
  distractor retrieval requires the cleaned S/M corpus.
- 2026-09-03: The first benchmark-only LoCoMo slice ran the pinned
  `conv-26/category=4` source as 19 session documents and 70 single-hop cases.
  The explicit token-overlap transport baseline produced
  `Recall-all@5=.9143` and `MRR=.7681`, with three standard artifacts and no raw
  conversation text in predictions. It is not a ServiceEpisode score or a
  BGE/RRF selection result.
- 2026-09-03: The same 70 cases then ran through token overlap, pinned BGE-M3
  cosine, and fixed lexical+dense RRF. Their Recall-all@5 values were `.9143`,
  `.8714`, and `.9286`; RRF MRR was `.7767`. All manifests say
  `DEV_DIAGNOSTIC` and `production_service_episode_semantics=NOT_EVALUATED`.
- 2026-09-03: The production ServiceEpisode corpus audit found one reusable
  synthetic canonical transport fixture but zero lifecycle-backed evaluation
  episodes with a real Case Owner acceptance receipt and resolvable source
  events. It remains valid for canonical commit/projection/generation smoke,
  but no production Memory retrieval score will be reported from it.
- 2026-09-03: The upstream blocker was localized to the Case Owner rather than
  retrieval. Ticket persistence has status and free-text notes but no
  authenticated accepted resolution/outcome or dereferenceable source-turn
  binding. The next production Memory step therefore requires one real
  accepted case lifecycle before projection; no adapter or benchmark fixture
  is allowed to manufacture that fact.
- 2026-09-03: The Case Owner gap is now closed at its owner boundary. An
  authenticated assignee accepts resolution/outcomes; one transaction writes
  the immutable accepted fact, advances the conversation sequence, resolves
  the Ticket with CAS, and appends its audit event. A dedicated projector binds
  the recorded source turns, commits a canonical ServiceEpisode, rebuilds an
  immutable MODEL generation, and retrieves it for the same subject. Root and
  independent review passed; focused PostgreSQL suite: `39 passed`. API/worker
  composition and a real non-empty evaluation corpus remain separate work.
- 2026-09-03: Production composition now exposes the accepted-resolution fact
  through an admin-authenticated endpoint whose service Owner also requires the
  principal to be the assigned Case Owner. The explicit generation CLI drains
  all pending accepted facts in bounded batches before rebuilding, so an active
  generation cannot omit facts after the first batch. Focused API/CLI/PostgreSQL
  suite: `22 passed`; no startup worker or evaluation score is claimed.
- 2026-09-03: The locked `dp-screen-03` asset ran through the real local
  Tesseract 5.5.0 provider. Trigger, artifact, consumption, and outcome all
  passed; one OCR call produced grounded page-level provenance and all declared
  ASCII observation fragments were consumed. The manifest states
  `PROJECT_DIAGNOSTIC`, `locked_contract_score_eligible=false`, and
  `official_benchmarks=NOT_RUN`.
- 2026-09-03: The same project diagnostic now resolves the declared region to
  original-page bbox `[720,0,1080,300]`, crops only that region for Tesseract,
  and retains page, region, crop-transform, and source checksum provenance.
  Missing region resolution returns typed `REGION_RESOLVER_UNAVAILABLE` rather
  than silently running whole-page OCR. Media suite: `69 passed, 4 skipped`.
- 2026-09-03: Fixed `fixed-512-64` Dev retrieval captured lexical and dense
  Top-40 once for each of 300 raw queries, then replayed the 15 declared fusion
  settings from the persisted artifact. All captures were `OK`; dense-only won
  with All-evidence Recall@20 `216/300=.7200`, Evidence Recall `.7211`, MRR
  `.4437`, and nDCG `.5078`. Dense-only makes RRF k irrelevant, so `k=10` is
  only the stable tie-break. No online default changed and all downstream RAG
  stages remain unexecuted.
- 2026-09-03: The live structured command baseline ran all 20 locked,
  attachment-free L0 clarification cases through `ChatApplication.handle()`.
  Provider calls/errors were `20/0`, but only `2/20` passed; 16 turns were
  classified as `NO_SUPPORTED_FLOW` instead of `CLARIFY`. Knowledge, Media,
  and Tool forbidden-invocation checks were each `20/20`. The run consumed
  8,426 input and 331 output tokens with mean/P95 latency
  `987.0ms/1361.3ms`. This is a Dev contract diagnostic and blocks cutover.
- 2026-09-03: One generic status-semantics repair defined CLARIFY as missing
  required context and NO_SUPPORTED_FLOW as a complete request outside the
  Registry. The viewed Dev slice progressed `2/20 → 11/20 → 13/20`; v3 used
  6,823 input and 261 output tokens with mean/P95 `913.8ms/1109.7ms`. Seven
  missing-context requests remain OOS, so tuning stopped and promotion remains
  blocked until an independent frozen conversation-heldout gate.
- 2026-09-03: `NO_SUPPORTED_FLOW` now compiles and publishes as a native
  command-primary OUT_OF_SCOPE policy terminal. A real chat-handler test proves
  legacy Intent, Agent, and Knowledge are skipped. Focused adjacent suite:
  `70 passed`; this owner repair does not change the `13/20` semantic score.
- 2026-09-03: The Knowledge Query Dev stage reused a checksum-bound 48-case
  transform artifact, then captured fresh Raw/Standalone Dense Top-40 from the
  frozen `fixed-512-64` BGE-M3 PostgreSQL generation. All 48 captures were OK.
  Raw-only reached All-evidence `.7500`; `.5/.5`, `.25/.75`, and standalone-first
  each reached `.8333`. The declared MRR/nDCG tie-break selected `0/1`, with the
  production rule retaining Raw for 23 identical/fallback cases. This is viewed
  Dev, not heldout; live rewrite, lexical retrieval, rerank, Parent, packing,
  generation, and judge were not run. Focused and adjacent suite: `34 passed`.
- 2026-09-03: A repository-wide Understanding data audit found no unconsumed,
  state-aware heldout with direct CLARIFY, Registry-backed RESOLVED, and OOS
  oracle. Existing public and project datasets remain valid for their declared
  legacy/component roles, but none can be relabelled after the fact as the
  command-primary promotion gate. Under the no-new-Gold scope, Intent remains
  `NOT_PROMOTED` and the Encoder correctness mode remains all-DEFER.
- 2026-09-03: The proposed multilingual BGE reranker is not locally runnable:
  its cache has a dangling revision ref but no config, tokenizer, or weights.
  The current 48-case Query cohort also does not meet the predeclared
  100-natural-multicondition / `.95` candidate-recall trigger. No reranker or
  Parent stage was opened; the complete English MiniLM remains historical only.
- 2026-09-03: Frozen Query candidates were replayed through the production
  ContextPacker and EvidencePack over six `K × budget` configurations. The
  viewed-Dev baseline selected `Top-5/2600`: Packed All-evidence was
  `31/48=.6458`, mean/P95 context tokens `2281/2560`, and packing harmful was
  zero. Candidate@20 was `.8333`, so the remaining `18.75pp` loss is caused by
  Candidate→Top-5 selection. Top-8 found more evidence before packing but its
  gain was eliminated by the same budget; Parent remains inapplicable.
- 2026-09-03: Existing public Knowledge Gold covers English only. The consumed
  Doc2Dial Dev can still report deterministic length slices: short `.7065`,
  medium `.6861`, and long `.8028` All-evidence@20. Chinese/code-switch project
  cases remain Trigger/Consumption smoke, not natural-distribution quality.
  MTRAG is not present. A conversation-isolated English heldout can be frozen
  mechanically from the unconsumed official Doc2Dial test pool after the Dev
  pipeline is fixed, without authoring new Gold.
- 2026-09-03: Public multimodal readiness was audited without manufacturing an
  adapter. No OmniDocBench, ViDoRe, or PM209 data/evaluator is present locally.
  The existing Tesseract path can support an English raster OCR-only
  OmniDocBench slice after those fixed artifacts arrive; layout/table/formula,
  visual page retrieval, and question-conditioned L2 remain missing production
  capabilities. Project `dp-screen-03` stays a project diagnostic and is not
  counted as an official score.
- 2026-09-03: Public Intent readiness was audited without rerunning consumed
  data. MASSIVE zh-CN is absent locally; BANKING77 is only an auto-mapped
  eight-legacy-label subset; CLINC150 contains only OOS examples collapsed to
  legacy `other`. Existing calibration/verification inputs have already been
  consumed and their workspace replay is only a legacy integrity regression,
  not an official score or command-primary promotion gate.
- 2026-09-03: The explicit structured production composition now accepts the
  existing CLARIFY terminal in addition to Knowledge. Default off, shadow, and
  legacy Knowledge-only behavior are unchanged. Product-a's ten viewed Dev
  cases ran `10/10` through the real provider with `10/0` calls/errors and no
  Knowledge, Media, or Tool invocation. This proves one composition slice, not
  a full `x/80` score.
- 2026-09-03: The frozen Knowledge baseline ran once on 120 independent
  Doc2Dial test conversations and all 488 official documents. All 120
  retrievals were `OK`; Candidate@20 All-evidence was `78/120=.6500` and
  Packed Top-5/2600 was `65/120=.5417`, with zero packing harm. An independent
  review reproduced all 211 Gold-span bindings and report metrics. This is an
  English `HELDOUT_FIXED_BASELINE`, not production promotion: it has no paired
  legacy result, no Chinese/code-switch score, and no generation/E2E gate.

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
- Stage 3 ServiceEpisode generation activation: `abb0fdb`.
- Stage 3 pinned BGE-M3 artifact verification: `12288d4`.
- Intent deterministic-boundary documentation: `23e1573`.
- Stage 4 current L1 media transport: `acf0478`.
- Stage 3 explicit generation rebuild CLI: `429cfe8`.
- Stage 3 Knowledge optional-scope canonicalization: `e3d4855`.
- Documentation/runtime alignment: `b06fe38`.
- Optional semantic runtime image: `e70a081`.
- Knowledge chunk-strategy ownership: `c746c00`.
- Calibrated command-encoder artifact contract: `7df9b6a`.
- Stage 3 task-owned L1 media execution: `33ecf99`.
- Raw PostgreSQL/BGE candidate evaluator: `65c3059`.
- Stage 3 command-primary clarification publication: `326b25a`.
- Candidate/clarification documentation alignment: `8670aba`.
- Direct migration runner import boundary: `75d1808`.
- CI PostgreSQL service with pgvector: `99af3cf`.
- Structured Registry-backed LLM command producer: `6c62aca`.
- CI command-artifact test dependencies: `fc58f93`.
- LoCoMo benchmark-session baseline: `bba8efb`.
- Structured command correctness runtime: `81f2271`.
- Structured correctness mode documentation: `264946a`.
- Knowledge Dev chunk selection: `83b3a8d`.
- LoCoMo BGE/RRF diagnostics: `036cee0`.
- Structured-command optional-dependency repair: `71075c1`.
- Grounded L1 project diagnostic: `c24603f`.
- Knowledge source capture and offline fusion replay: `19237ba`.
- Live locked L0 clarification diagnostic: `0c4d194`.
- Structured status-semantics contract: `6617853`.
- Command-primary out-of-scope terminal: `28ff1b6`.
- Explicit media region grounding: `285ff13`.
- Knowledge Query source capture and offline replay: `b38ad07`.
- Knowledge identity-selection and packing baseline: `0d3eecb`.
- Accepted Case Owner to ServiceEpisode projection: `46734c0`.
- Accepted resolution API and explicit projection drain: `2b7f5ff`.
- Structured clarification production composition: `9854b54`.
- Fixed English Knowledge heldout baseline: `193ede1`.
