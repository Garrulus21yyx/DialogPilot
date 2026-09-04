# DialogPilot Target Architecture v1 implementation

- Branch: `feat/customer-service-target-architecture`
- Started: 2026-09-03
- Goal: replace the current command-primary orchestration path with one coherent
  conversation-owned runtime that selects the shortest safe execution shape:
  deterministic/direct, one delegated domain agent, multi-agent work graph, or
  persistent workflow.
- Compatibility: legacy routing and orchestration contracts are not a design
  constraint. Existing unrelated in-progress retrieval work must remain untouched.

## Positive target contract

1. Conversation state has one lifecycle owner and is versioned with compare-and-set.
2. A turn is resolved deterministically when pending interaction, approval, resume,
   cancellation, or a unique active workstream supplies enough authority.
3. A validated turn plan owns both control-plane mutations and a dependency graph of
   work, while business side effects exist only in governed tool executions.
4. A work item chooses exactly one control mode: `DIRECT`, `DELEGATED`, or `WORKFLOW`.
   Skills are optional; direct tools and delegated planning are first-class.
5. Domain agents can select only registered tools/skills in the work-item capability
   envelope and return typed `AgentResult` values. They do not own delivery.
6. Independent read work may run concurrently; dependent or same-aggregate writes are
   serialized. Successful independent outcomes survive peer failure.
7. Cross-agent facts are structured, sourced, versioned, and merged by requirement
   authority rather than by prose concatenation or majority vote.
8. Missing user input, missing external evidence, approval, retryable failure, unknown
   write outcome, and terminal failure remain distinct typed outcomes.
9. Publication is the sole delivery boundary but may be a zero-LLM deterministic path.
10. LangGraph checkpoints own execution position only; event, flow, fact, operation,
    receipt, publication, and delivery stores remain business authorities.

## Causal surface and owners

| Concern | Owner | Principal consumers |
|---|---|---|
| Conversation lifecycle | Conversation Manager | resolver, compiler, publication |
| Capability/risk/schema | Versioned capability registry | policy, agents, tool runtime |
| Turn meaning proposal | deterministic resolver / structured router | route policy |
| Accepted command and state legality | Route policy | turn-plan compiler |
| Work dependencies and dispatch | parent orchestrator graph | domain worker graphs |
| Business side effects | governed tool runtime | receipt ledger, result board |
| Requirement facts | result board with authority policy | verifier, publication |
| User-visible command | publication | delivery |

## Stages

| Stage | Status | Deliverable | Verification | Commit |
|---|---|---|---|---|
| 0 | done | Repository audit and this implementation ledger | dirty-worktree and dependency audit | `f6ee532` |
| 1 | done | New registry, work-item, agent-result, fact/evidence, and interaction contracts | 8 contract tests | `7682bf3` |
| 2 | done | Conversation state, workstreams, pending interaction, and deterministic resolver with CAS semantics | 7 state-machine tests; 15 cumulative | `8954756` |
| 3 | done | Turn policy/compiler producing direct, delegated, multi-domain, and workflow plans | 7 compiler/policy tests; 22 cumulative | `12bc26b` |
| 4 | done | Parent orchestration graph, direct runtime, delegated domain workers, result board, and partial failure | 5 graph integration tests; 27 cumulative | `eee65a0` |
| 5 | done | Governed write workflows, approval binding, operation keys, receipts, and reconciliation | 9 write state-machine/integration tests; 36 cumulative | `499e906` |
| 6 | done | Handoff commit/receipt, human ownership, and single publication command path | 5 handoff/publication tests; 41 cumulative | `1ce7a6f` |
| 7 | done | Encoder fast path and memory/media evidence routing | 6 precision/binding tests; 47 cumulative | `6b54922` |
| 8 | done | Six in-memory vertical E2E scenarios, default registry, capability-scoped safety gates, and measured report | 7 E2E tests; 54 cumulative | `e66facd` |
| 9 | done | New ConversationManager, durable PostgreSQL event-backed state/operation adapters, and LangGraph checkpointer | 7 local persistence/resume tests; 61 cumulative; 1 real-PostgreSQL test skipped without database URL | `b0886a9` |
| 10 | done | `/chat` read-path cutover to Target v1, synchronous target admission, trusted tool context, and removal of legacy fallback authority | 4 local HTTP/cutover tests; 65 cumulative; 2 real-PostgreSQL tests skipped without database URL | `b57e02f` |
| 11 | done | Real PostgreSQL/HTTP six-scenario E2E, async checkpoint ownership, committed handoff workflow completion, and documentation convergence | 68 Target tests pass against PostgreSQL; repository suite 1120 passed / 6 unrelated dirty-RAG contract failures | `9a19ce8` |
| 12A | done | Refund preparation, persisted approval, deterministic resume, governed commit, rejection/expiry/stale handling | 75 Target tests; repository 1127 passed / 6 unrelated dirty-RAG failures | `57658ce` |
| 12B | done | Public retry/poll entry for `OUTCOME_UNKNOWN`, stable operation binding, authoritative reconciliation, and terminal Receipt publication | 76 Target tests; repository 1128 passed / 6 unrelated dirty-RAG failures | this stage commit |
| 13A | done | Real `media_read` and `catalog_search` tools plus a Product skill executor that passes verified media observations into catalog lookup | Product owner/tool contract tests | this stage commit |
| 13B | done | Real upload-to-chat Product E2E, typed unavailable/no-match/ambiguous paths, documentation, commit and push | 80 Target tests including real PostgreSQL Asset/HTTP E2E | this stage commit |
| 14A | done | Target-native structured semantic router and typed provider boundary behind deterministic resolution | 66 focused Target tests; real HTTP/PostgreSQL semantic fallback; provider failure remains typed | this stage commit |
| 14B | done | Target Skill/Command Encoder dataset, class-scoped calibration, heldout gate and public fast-path binding | 91 Target tests; reproducible training; real HTTP/PostgreSQL Encoder bypass | this stage commit |
| 15 | done | Target-native evaluation funnel, capability-scoped gates, observability and architecture/runbook convergence | 96 Target tests; repository 1148 passed / 6 unrelated dirty-RAG failures | this stage commit |
| 16A | done | Separate logistics, refund policy, refund eligibility and invoice read commands from refund execution | unit contracts plus real HTTP/PostgreSQL read-path E2E | this stage commit |
| 16B | done | Generic Product QA task contract; category and attributes remain evidence data rather than Skill identities | 93 Target tests including real PostgreSQL/HTTP boundaries | this stage commit |
| 17 | done | Registry-owned generic write-preparation binding with no refund fields in ConversationManager | 95 Target tests including real PostgreSQL/HTTP boundaries | this stage commit |
| 18 | done | WorkItem-bound Action identity and Registry approval policy; workflow executor no longer branches on Flow names | 96 Target tests including real PostgreSQL/HTTP boundaries | this stage commit |

## Stage record

### Stage 0 — audit

- Observed: the branch tracks `origin/feat/customer-service-target-architecture` at
  `5b8f417` and has extensive unrelated modified/untracked retrieval and evaluation
  work. Every architecture commit must use an explicit path allowlist.
- Observed: `langgraph` is not currently declared in `requirements.txt`.
- Observed: the repository already has useful lower-level authorities for tool
  allowlists, approvals, operation identities, receipts, flow persistence,
  publication, and delivery; the replacement should converge their contracts rather
  than introduce a second authority.

## Files produced by stage

- Stage 0: `plans/target-architecture-v1-implementation-2026-09-03.md`
- Stage 1: `application/capability_registry.py`, `application/work_item.py`,
  `application/agent_result.py`, `tests/test_target_architecture_contracts.py`
- Stage 2: `application/conversation_state.py`,
  `application/deterministic_resolution.py`,
  `tests/test_conversation_state_resolution.py`
- Stage 3: `application/turn_planning.py`, extensions to the Target v1 registry
  and work-plan contracts, `tests/test_target_turn_planning.py`
- Stage 4: `application/orchestration_runtime.py`,
  `application/result_board.py`, LangGraph dependency and orchestration tests
- Stage 5: `application/write_workflow.py`, operation/approval/receipt/
  reconciliation state-machine tests and LangGraph worker integration
- Stage 6: `application/handoff_runtime.py`, human ownership transition,
  ticket-receipt-gated publication and handoff tests
- Stage 7: `application/encoder_fast_path.py`, precision-first encoder and
  bounded Memory/Media evidence-routing tests
- Stage 8: `application/default_capability_registry.py`,
  `application/capability_safety.py`, six vertical E2E scenarios and
  capability-scoped safety-gate tests
- Stage 8 report: `docs/target-architecture-v1-core-report.zh-CN.md`
- Stage 9: `application/target_conversation_manager.py`, durable state and
  operation event adapters in `infrastructure/postgres_target_runtime.py`,
  explicit LangGraph PostgreSQL checkpoint lifecycle/serializer, and
  invocation-scoped checkpoint replay.
- Stage 10: `application/target_chat_application.py`, bounded fast-path
  understanding, governed read-tool adapter, synchronous Target admission,
  PostgreSQL publication adapter, and `/chat` composition cutover. Refund
  mutation remains capability-disabled until a subsequent eligibility/approval
  resume/write stage closes the full side-effect lifecycle.
- Stage 11: real ASGI `/chat` + PostgreSQL six-scenario E2E, async LangGraph
  checkpoint lifecycle, governed `support_ticket_create`, Receipt-gated human
  ownership transfer, workflow completion, and a schema-valid deterministic
  no-index publication fingerprint.
- Stage 13: versioned local Product Catalog owner, governed `media_read` and
  `catalog_search` tools, Product composite Skill executor, and real upload →
  PostgreSQL Asset → OCR evidence → Catalog Fact → `/chat` publication E2E.
- Stage 14A: Target-native structured semantic schema, Anthropic-compatible
  provider adapter, deterministic Registry command compilation, bounded-first
  cascade, typed provider failures, and real `/chat` + PostgreSQL fallback E2E.
- Stage 14B: Target-native four-way supervision data, reproducible hashed
  character n-gram classifier, class-scoped calibration/heldout gates,
  dependency-free checked artifact loading, semantic-signal constraints, and
  public Encoder → single Worker execution.
- Stage 15: six-layer Target evaluation funnel, required-evidence safety gates,
  persisted response-level evaluation traces, and an operational runbook.
- Stage 16A: read-only logistics, refund policy, refund eligibility and invoice
  command bindings; refund eligibility queries no longer start refund workflows.
- Stage 16B: one task-level `product_qa` capability shared across product
  categories; category names and category-specific attributes remain catalog/RAG
  evidence rather than global router fields, Skill identities, or fixed missing-input
  schemas.
- Stage 17: Action Registry owns the authoritative preparation tool, requirement,
  readiness predicate, source entity-version field and write-argument binding;
  ConversationManager applies that typed contract without knowing refund semantics.
- Stage 18: accepted write WorkItems carry the Action identity and approval policy
  selected by RoutePolicy; workflow execution grants authority by that policy rather
  than by recognizing a refund or handoff Flow name.

## Scope correction after executable-core review

The original eight-stage list ended at in-memory E2E. That is not sufficient to
claim migration completion because the public API still owns the old runtime path.
Stages 9-11 were added to cover the actual remaining causal surface: durable owners,
API cutover, removal of duplicated authorities, and real boundary E2E.

## Bounded v1 omissions after stage 12B

- The bounded refund flow now covers preparation, explicit approval, committed
  execution, declined/expired/stale signals, `OUTCOME_UNKNOWN`, public polling,
  authoritative status reconciliation, and terminal Receipt publication.
- Product identification now uses registered local-project adapters backed by the
  PostgreSQL Asset owner, OCR evidence and a versioned catalog generation. The bounded
  v1 identifies printed model labels; general visual-only recognition still requires
  the configured VLM extension and is not claimed by this stage.
- The public understanding path runs deterministic bounded resolution before a
  structured semantic provider. Provider output may propose only Target-native goals;
  Registry-owned compilation supplies tools, effects, risks and requirements. A real
  encoder fast path is not yet claimed.
- Legacy modules remain in the repository for non-`/chat` consumers. The `/chat`
  accessor has no legacy fallback authority.

## Stage 11 verification notes

- Real boundary command:
  `TEST_DATABASE_URL=... pytest -q tests/test_target_http_postgres_e2e.py`
  passed all six scenarios through ASGI and PostgreSQL.
- Target cumulative command passed 68 tests covering contracts, state resolution,
  planning, orchestration, writes, handoff, persistence, cutover, and HTTP E2E.
- Repository command passed 1120 tests and failed 6 tests. Every failure has the
  same pre-existing dirty-worktree cause: three new retrieval-policy keys are
  emitted by `core/rag_policy.py` but are not yet admitted by the legacy
  `services.evolution.bundle` whitelist. Stage 11 does not own or stage that work.
- Source-boundary review confirms `_chat_application()` has no legacy coordinator
  fallback; direct order work remains zero-agent; independent two-domain work is the
  only tested multi-worker shape; business state and operation receipts remain in
  PostgreSQL rather than LangGraph checkpoints.

## Stage 12 positive contract — refund approval and resume

1. The initial refund command creates one versioned `execute_refund:v1`
   workstream and a stable operation identity; it cannot invoke the write tool.
2. Eligibility must succeed before the conversation owner creates one
   `PendingApprovalState` bound to workstream version, action, operation key,
   target entity and target entity version.
3. Publication emits an `InteractionRequestCommand`; a normal final response is
   not an approval signal and cannot grant authority.
4. A later authenticated `/chat` request carries an explicit approval decision
   and signal identity. The deterministic resolver consumes it exactly once;
   stale, cross-conversation and duplicate signals fail with typed conflicts.
5. Only an approved consumed signal may construct an `ApprovalGrant`. Resume
   reuses the original operation key and target version and continues the
   existing workstream; it never starts a second workstream.
6. Rejection cancels the workstream without calling `refund_request_create`.
   Commit requires a typed Receipt; transport uncertainty enters reconciliation
   and never blind-retries the write.
7. LangGraph checkpoint remains execution-position state only. Pending approval,
   operation state and Receipt remain PostgreSQL business facts.

### Audit finding

The audit gap has been closed for committed, declined, expired and stale approval
paths. `ChatRequest` now carries an explicit signal identity and decision;
`PREPARE_WORKFLOW` and `CONTINUE_WORKFLOW` separate read preparation from the
write; `PendingApprovalState` and `AcceptedApprovalState` preserve the exact
operation/action/entity/version binding; and RoutePolicy rejects any modified
continuation.

## Stage 12B verification notes

- A write transport timeout persists `OUTCOME_UNKNOWN` in the operation ledger and
  advances the conversation-owned Workstream to `RECONCILING/RECONCILE`.
- A later request with a new request identity and the same accepted approval binding
  deterministically rebuilds the original operation contract. It calls the registered
  read-only `refund_status` authority with the original operation key before any
  execution decision. The business owner performs an exact user/conversation/order/
  idempotency-key lookup, so an older refund for the same order cannot satisfy the poll.
- The operation ledger binds `operation_key` to `operation_fingerprint`, which excludes
  turn-local identity, snapshot and execution budget but includes arguments, target
  version, action envelope, registry, approval, flow and reconciliation policy.
  Therefore legitimate cross-turn reconciliation is stable while changed mutation
  semantics fail with `OperationConflict`.
- Real ASGI/PostgreSQL E2E proves the uncertain mutation is invoked once, reconciliation
  uses the status tool, a committed Receipt is published, and the Workstream terminates
  as `COMPLETED/COMPLETE`.
- The cumulative Target suite passes 76 tests. The repository suite passes 1128 tests
  and retains the same 6 unrelated dirty-RAG `AgentBundle` whitelist failures recorded
  in stage 12A; stage 12B introduces no new failing test.

## Stage 14A verification notes

- A bounded direct order request performs zero semantic-provider calls. A paraphrased
  request unresolved by the bounded layer invokes the provider exactly once and is
  compiled into the registered `order_lookup` direct work item; no Agent dispatch is
  introduced for this simple path.
- Provider-returned entity identifiers must already occur in trusted structured
  observations or the current message. Unknown goal kinds, invented entities and
  malformed outputs fail as `INVALID_PROVIDER_OUTPUT` before RoutePolicy.
- Provider transport failures remain retryable typed application failures and invalid
  output remains a non-retryable typed application failure. Neither is published as a
  request for more user information, and neither can execute a tool.
- The focused Target suite passed 66 tests, including real ASGI `/chat`, PostgreSQL
  admission/state/publication boundaries and the semantic fallback case.

## Stage 14B verification notes

- The Target artifact is trained only against `general_qa`,
  `product_identification`, `refund_status_summary`, and `__DEFER__`; it does not
  reuse or translate the legacy 53-label Intent taxonomy.
- Calibration selects a separate threshold for each read Skill. A class is enabled
  only when its calibration one-sided 95% Wilson lower bound is at least `0.88`,
  heldout accepted precision is at least `0.98`, and heldout acceptance support is
  at least 10. Only `refund_status_summary` passed both gates (13/13 accepted
  heldout cases); General and Product remain capability-scoped deferred paths.
- The model is a learned hashed character n-gram logistic regression exported as
  checked JSON. Online inference has no sklearn dependency; the loader binds model
  digest, dataset digests, bundle version and live read-Skill ownership/effect.
- A class-specific observed-signal gate prevents generic order progress language
  from becoming a refund query. Missing entity, active workstream, disabled class,
  boundary/low margin, write language and multi-domain language defer to the
  structured semantic router.
- Real ASGI/PostgreSQL E2E proves a semantic refund-status utterance bypasses the
  provider, dispatches exactly one Billing worker, and publishes verified tool
  evidence. A generic order-progress paraphrase invokes the provider exactly once
  and remains a zero-Agent direct tool path.
- The complete Target-focused suite passed 91 tests against PostgreSQL. The dataset
  is synthetic prototype evidence and is not represented as production traffic or
  an external benchmark.

## Stage 15 verification notes

- Every completed response now carries one bounded `target-evaluation-trace-v1`
  projection across Trigger, Artifact, Consumption, State/Side-effect, Outcome and
  Cost. It records identifiers, typed states and capability envelopes, never prompts,
  raw tool output, credentials or user secrets.
- `CapabilitySafetyGate` accepts an explicit required-invariant profile per
  capability. Missing proof fails that capability with a stable
  `missing:<capability>:<invariant>` evidence ref; unrelated capabilities retain
  their own decisions.
- `TargetArchitectureEvaluator` emits six independent layer checks and a separate
  hard gate. A functionally correct response with an unauthorized-tool observation
  remains failed; there is no weighted score that can offset it.
- The complete Target-focused suite passed 96 tests against PostgreSQL. The full
  repository passed 1148 tests and failed the same 6 dirty-RAG `AgentBundle`
  whitelist tests: the uncommitted retrieval owner emits `expansion_query_weight`,
  `query_expansion_count`, and `metadata_hint_weight`, while the legacy bundle
  whitelist has not yet been migrated. Stage 15 does not own or stage that work.

## Stage 16A verification notes

- `order_lookup` remains the single business authority for both order and logistics
  status; no wrapper Skill or Agent dispatch was added to the direct path.
- Refund policy and invoice questions use separate Billing-owned knowledge Skills so
  they remain independently measurable while sharing the governed Knowledge tool.
- Refund eligibility is a direct read of `refund_eligibility_check`. The positive
  contract test proves that “订单 DP1234 能退款吗” produces no Flow transition,
  PendingApproval or write capability. Explicit execution language still compiles a
  `PREPARE_WORKFLOW` mutation for `execute_refund:v1`.
- Structured goals cannot invent an order ID. Real ASGI/PostgreSQL tests cover the
  eligibility, policy and invoice paths and retain the shortest execution shapes.

## Stage 16B verification notes

- Root cause corrected: an installation example had leaked category-specific fields
  (`wall_material`, installation width and wet-area constraints) into the global
  semantic router, capability registry, Product Catalog owner and tool contract.
  Those uncommitted specializations were removed together rather than retained as
  a category Skill layer.
- The Product domain now exposes task-level `product_identification` and
  `product_qa` Skills. Keyboard compatibility, apparel sizing and camera-network
  questions compile to the same `product_qa` WorkItem and governed
  `knowledge_search` evidence path.
- Product category and arbitrary category attributes are retrieval/catalog data.
  A new category does not require a Router goal, Agent, Skill, missing-input field,
  or tool registration. A distinct Skill remains justified only by a different
  reusable execution contract; a Flow remains justified by controlled cross-turn
  state or side effects.
- The bounded path now reserves missing-media clarification for actual image
  identification language. Generic Product questions defer to structured semantic
  routing instead of being recast as identification requests.
- The complete Target suite passed 93 tests, including real ASGI/PostgreSQL state,
  write, Product upload, orchestration and publication boundaries.
- The repository-wide suite passed 1156 tests and retained the same 6 failures from
  the unrelated uncommitted RAG-policy migration: the policy producer emits
  `expansion_query_weight`, `query_expansion_count` and `metadata_hint_weight`, while
  the legacy `AgentBundle` consumer does not yet admit those keys.

## Stage 17 verification notes

- Observed root cause: `PREPARE_WORKFLOW` looked reusable, but command proposals and
  ConversationManager jointly owned refund-specific preparation semantics through
  `refund_eligibility_check`, `eligible`, `order_version` and
  `expected_order_version`. Every second write Flow would otherwise require another
  business branch in the conversation lifecycle owner.
- `ActionPreparationDefinition` is now the single versioned owner of the preparation
  tool, read requirement, readiness field/value, target-version source and target
  write argument. Registry construction verifies that its tool is read-only,
  authorized by the requirement, and inside both Flow and Agent allowlists.
- Routers now propose the user goal and action identity only. RoutePolicy resolves
  the preparation capability from Registry; `FlowMutation` carries the accepted
  immutable binding into ConversationManager.
- ConversationManager reads only the declared requirement and fields. A positive
  invariant test replaces the refund names with `permitted`, `revision_token` and
  `expected_revision`; approval creation and version binding still succeed without
  a caller-side compatibility branch.
- The complete Target suite passed 95 tests, including real ASGI/PostgreSQL state,
  workflow, Product, orchestration and publication boundaries.

## Stage 18 verification notes

- Observed root cause: `TargetWorkflowExecutor` granted
  `USER_COMMAND_SUFFICIENT` only when `flow_ref == human_handoff:v1`. That made a
  specific Flow name a hidden second approval authority beside Registry and prevented
  new actions from inheriting their declared policy.
- TurnPlanCompiler now binds `action_ref` and the validated `ApprovalPolicy` into
  every write WorkItem. Both fields participate in work/operation fingerprints and
  are mandatory write safety bindings.
- Workflow execution grants a user-command approval for any Action whose compiled
  policy is `USER_COMMAND_SUFFICIENT`, accepts an approval-resume binding only for
  `EXPLICIT_CONFIRMATION_REQUIRED`, and leaves stronger policies fail-closed until
  their own trusted grant adapters exist.
- A policy-invariance test renames the handoff Flow to an unrelated version and
  still obtains the same grant. The complete Target suite passed 96 tests, including
  real ASGI/PostgreSQL refund, handoff, reconciliation and Publication boundaries.
