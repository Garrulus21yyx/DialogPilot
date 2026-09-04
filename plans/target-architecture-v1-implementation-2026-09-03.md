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
| 12B | done | Public retry/poll entry for `OUTCOME_UNKNOWN`, stable operation binding, authoritative reconciliation, and terminal Receipt publication | 76 Target tests; repository 1128 passed / 6 unrelated dirty-RAG failures | `c81e633` |
| 13 | done | Real upload-to-chat Product E2E, `media_read`/`catalog_search`, one reusable Product skill, and typed failure paths | 80 Target tests including real PostgreSQL Asset/HTTP E2E | `abe2975` |
| 14A | done | Target-native structured semantic router and typed provider boundary behind deterministic resolution | 66 focused Target tests; real HTTP/PostgreSQL semantic fallback; provider failure remains typed | `c748c7a` |
| 14B | done | Target Skill/Command Encoder dataset, class-scoped calibration, heldout gate and public fast-path binding | 91 Target tests; reproducible training; real HTTP/PostgreSQL Encoder bypass | `81ec8bc` |
| 15 | done | Target-native evaluation funnel, capability-scoped gates, observability and architecture/runbook convergence | 96 Target tests; repository 1148 passed / 6 unrelated dirty-RAG failures | `d1b9387` |
| 16A | done | Separate logistics, refund policy, refund eligibility and invoice read commands from refund execution | unit contracts plus real HTTP/PostgreSQL read-path E2E | `09e0001` |
| 16B | done | Generic Product QA task contract; category and attributes remain evidence data rather than Skill identities | 93 Target tests including real PostgreSQL/HTTP boundaries | `888a9e4` |
| 17 | done | Registry-owned generic write-preparation binding with no refund fields in ConversationManager | 95 Target tests including real PostgreSQL/HTTP boundaries | `5407303` |
| 18 | done | WorkItem-bound Action identity and Registry approval policy; workflow executor no longer branches on Flow names | 96 Target tests including real PostgreSQL/HTTP boundaries | `2b9b28d` |
| 19 | done | Registry-owned reconciliation contracts plus operation-bound Handoff lookup; no Flow-name branches in workflow execution | 113 Target/tool/authority tests with real PostgreSQL/HTTP boundaries | `c15d463` |
| 20 | done | Order cancellation as a second governed write Flow using the generic preparation, approval, Receipt and reconciliation contracts | 136 Target/owner/tool/authority tests with real PostgreSQL/HTTP boundaries | `78808ef` |
| 21 | done | Generic typed missing-input aggregation, durable suspended read work, exact signal resume, and one Interaction publication across domains | 125 focused Target/owner/tool tests with real PostgreSQL enabled | `ad6392b` |
| 22 | done | Registry-governed `NEEDS_EVIDENCE` resolution and bounded same-WorkItem resume without user clarification | 130 focused Target/owner/tool tests with real PostgreSQL enabled | `f31814d` |
| 23 | done | Governed shipping-address change Flow with authoritative order version, explicit confirmation, idempotent Receipt and reconciliation | 146 focused Target/owner/tool/authority tests with real PostgreSQL enabled | `5c1732d` |
| 24 | done | Account-security reads and governed freeze Flow with trusted principal, version binding, Receipt and reconciliation | 180 Target and affected-consumer tests with real PostgreSQL enabled | `d991360` |
| 25 | done | Remove one-Tool pseudo-Skills and let the calibrated Encoder fast path select registered Tool or composite Skill capabilities | 186 Target and affected-consumer tests with real PostgreSQL enabled | `22d5997` |
| 26 | done | Existing ReAct Agent execution behind delegated WorkItems with Tool-envelope enforcement | 71 focused tests | `fdf02e9` |
| 27 | done | Dynamic optional composite-Skill selection inside the existing ReAct loop | 90 focused tests | `4e766c9` |
| 28 | done | Bounded current-turn context and one-shot demand-driven ServiceEpisode retrieval | 69 focused tests | `24acc54` |
| 29 | done | Shared generic L1 OCR/L2 VLM evidence Tools with no product-category rules | 97 focused tests | `0fa28b8` |
| 30 | done | Registry-scoped security preemption of interruptible business writes | 64 focused tests | `85eab23` |
| 31 | done | LangGraph native interrupt/resume for typed user input and workflow approval signals | 26 Manager/HTTP/PostgreSQL tests plus native graph state assertions | `c541693` |

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
- Stage 19: each Action owns a typed reconciliation read contract; refund and Handoff
  unknown outcomes use the same executor algorithm, and Handoff can query the ticket
  owner by its exact operation key without replaying ticket creation.
- Stage 20: `cancel_order:v1` proves the generic write lifecycle with a second domain
  Action, while approval resume now derives owner, requirements and Flow from the
  bound pending Action/Workstream instead of assuming every approval is a refund.
- Stage 25: the default Registry retains only the genuinely composite
  `product_identification` Skill. Policy, invoice, Product QA and refund-status reads
  use their atomic Tools directly; Encoder artifacts bind an explicit Tool-or-Skill
  capability kind rather than requiring fake Skill wrappers.

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

## Stage 19 verification notes

- Observed root cause: `_ToolReconciler` recognized `execute_refund:v1` and embedded
  refund parameter and Receipt fields. `reconciliation_policy` was only an opaque
  label, so Registry could not validate or execute another Action's unknown outcome.
- `ActionReconciliationDefinition` now owns the read tool, read requirement,
  operation-key request/response fields, source arguments that must round-trip, and
  Receipt identity field. Registry rejects write tools or unauthorized requirements
  in that role; the complete definition is compiled into the WorkItem safety envelope
  and both work and operation fingerprints.
- `_ToolReconciler` now executes one generic algorithm and contains no refund or
  handoff Flow names. It accepts COMMITTED only when authority, operation key,
  passthrough entity fields and Receipt identity all match the registered contract.
- Handoff gained `support_ticket_by_operation`, backed by a user-and-conversation
  scoped PostgreSQL lookup of the exact idempotency key. Target ticket creation binds
  that same business operation key; unknown transport outcomes can therefore be
  observed without blindly repeating the write.
- A renamed-Flow invariant test proves reconciliation is selected by the compiled
  Action contract. 113 Target, tool, authority and real PostgreSQL/HTTP tests passed.
  The isolated lifespan test remains blocked before tool assertions by the unrelated
  dirty RAG/legacy `AgentBundle` key mismatch already recorded in Stage 16B.

## Stage 20 verification notes

- CustomerOperations is the single order-cancellation owner. It rechecks trusted user,
  current order version and cancellable `paid` state in one transaction, advances the
  order version, records one idempotent cancellation Receipt and supports exact
  operation-key lookup for reconciliation.
- Registry defines `cancel_order:v1` and `order.cancel:v1`. Its preparation reads
  `order.current_state`, compares the registered `status == paid` predicate, binds
  `version` to `expected_order_version`, requires explicit confirmation, executes
  `order_cancel`, and reconciles through `order_cancel_status`.
- The bounded and structured routers only propose the cancellation goal and observed
  order ID. They do not select tools, approval semantics, version fields or retry
  behavior. Ordinary order-status queries retain the zero-Agent direct path.
- Approval resume is now domain-neutral: it resolves Action owner, requirements and
  the active Workstream Flow from persisted state. A two-turn state test proves an
  order cancellation resumes `order_logistics`, not Billing.
- Real ASGI/PostgreSQL E2E verifies precheck returns an interaction, confirmation
  executes exactly one `order_cancel` call as the General tool principal, Receipt
  publication completes, and no second Agent is dispatched. Structured routing uses
  the same Flow contract; 136 relevant tests passed.
- Approval decline and expiry publication is action-neutral, so cancelling or timing
  out a non-refund Action cannot produce refund-specific user-visible text.

## Stage 21 verification notes

- The product boundary remains task-level: `product_identification` and `product_qa`
  are shared by all catalog categories. Product category and arbitrary attributes are
  evidence/filter data; no category-specific Skill, Router goal, state field or tool
  was added. Architecture tests no longer use an installation-specific capability as
  the global missing-input example.
- The contract mismatch between `MissingInputSpec.target_work_item_id` and persisted
  Workstream identity is closed at the Conversation owner. A pending interaction now
  durably suspends the exact read WorkItems that requested input; legacy Flow-bound
  fields remain explicitly separate rather than pretending a WorkItem is a Flow.
- ConversationManager aggregates all required fields from independent AgentResults
  into one `PendingInteractionState`. PostgreSQL serialization preserves the complete
  capability envelope and its fingerprint, including direct Tool versus delegated
  task-level Skill execution.
- Resume requires the exact interaction id, version, WorkItem id and field name. The
  deterministic resolver binds values to the suspended items, and bounded
  understanding reconstructs Registry-validated commands without reclassifying the
  user's task. Stale or cross-interaction replies fail with a typed conflict.
- Publication emits one `FIELDS` InteractionRequest with a machine-readable resume
  schema. Admission fingerprints include the interaction payload, and replay restores
  the original interaction kind instead of labeling every interaction as approval.
- Registry-derived Tool risk is retained for freely delegated read work even when no
  Skill hint is present. The focused Target suite passed 125 tests with PostgreSQL
  enabled, covering state codec, exact resume, multi-domain aggregation, public
  publication, partial execution, writes and existing real HTTP boundaries.

## Stage 22 verification notes

- `NEEDS_EVIDENCE` is resolved inside the deterministic orchestration runtime, not by
  ConversationManager and not by asking the user. Existing shared facts are reused
  first; otherwise a configured Evidence Resolver receives the typed requirement,
  preferred providers and explicit arguments.
- Evidence recovery is bounded by the WorkItem step budget and by a request signature,
  so a Worker cannot create an unbounded Agent → Evidence → Agent loop. No evidence
  preserves the typed `NEEDS_EVIDENCE` outcome and dependency blocking semantics.
- `TargetEvidenceResolver` accepts only a registered read Tool that is simultaneously
  inside the WorkItem allowlist, the requirement authority allowlist and the preferred
  provider list. Its returned facts must match the requested requirement; substitution
  fails closed.
- Resolved facts retain source and producer versions and are merged into the final
  Result Board. A domain Worker is then resumed with the same WorkItem and enriched
  `AgentContextView`; no extra global routing or multi-Agent dispatch occurs.
- The public composition root now supplies this Registry-backed resolver to the static
  LangGraph parent runtime. The focused Target suite passed 130 tests with PostgreSQL
  enabled, including positive resolution, no-evidence termination, forbidden provider,
  wrong-requirement, partial failure and existing HTTP/write boundaries.

## Stage 23 verification notes

- `CustomerOperationsService` owns the shipping address and its monotonic order
  version. Address change rechecks trusted user scope, exact order version and the
  currently supported `paid` state in one transaction, then records an idempotent
  operation Receipt. Reusing an operation key with different address content fails
  with the existing typed idempotency conflict.
- Atomic `shipping_address_change` and read-only
  `shipping_address_change_status` tools are registered with explicit schemas,
  authorities and output versions. Reconciliation queries the exact scoped operation
  key and verifies both order id and address; write transport uncertainty therefore
  cannot cause blind replay.
- Registry adds `change_shipping_address:v1` and
  `order.shipping_address.change:v1`. The existing generic preparation contract reads
  `order.current_state`, binds its version, applies explicit-confirmation policy and
  compiles only the two address tools into the write WorkItem.
- Bounded and structured understanding propose an address-change goal only when the
  order id and address originate in the user input. An invented semantic-provider
  address is rejected. This is a Flow/Action, not an address Skill and not a product
  category capability.
- Real ASGI/PostgreSQL E2E covers prepare → InteractionRequest → exact approval → one
  committed write Receipt through the same ConversationManager and Publication path.
  The focused Target, business-owner, tool and authority suite passed 146 tests with
  PostgreSQL enabled.

## Stage 24 verification notes

- Account security remains task-oriented rather than incident-specific: recent-event
  review is one atomic read Tool, while account freeze is a registered high-risk
  Flow/Action. No security Skill or unconditional multi-Agent dispatch was introduced.
- `CustomerOperationsService` owns the authoritative versioned account status, atomic
  active-to-frozen transition and idempotent freeze Receipt. Both the write and exact
  operation lookup are scoped to the trusted authenticated user; stale versions,
  already-frozen state and cross-user lookups fail closed with typed outcomes.
- Registry defines `freeze_account:v1` and `account.freeze:v1`. Generic preparation
  reads `account.current_state`, checks `status == active`, binds the observed version
  to `expected_account_version`, requires explicit confirmation, and reconciles by the
  exact operation key through `account_freeze_status`.
- Bounded and structured understanding only propose `security_review` or
  `freeze_account`. Tool identity, approval policy, version binding and retry behavior
  remain Registry-owned. `TargetWorkflowExecutor` maps the accepted
  `account_security` owner to its governed Tool principal.
- Real ASGI/PostgreSQL E2E proves security review is a direct read and freeze follows
  prepare → InteractionRequest → approval → one committed write Receipt. The expanded
  Target and affected-consumer suite passed 180 tests with PostgreSQL enabled.

## Stage 25 verification notes

- The default capability bundle now contains one composite Skill:
  `product_identification`, which combines media extraction and catalog resolution.
  The former one-Tool wrappers for general QA, Product QA, refund status, refund
  policy and invoice policy were removed from both Registry and Agent allowlists.
- Clear policy and Product knowledge questions compile directly to governed
  `knowledge_search`; refund status compiles directly to `refund_status`. These paths
  therefore run no domain Agent. Cross-domain turns project to `MIXED` when they
  contain both direct work and one genuinely delegated Product-identification task.
- Encoder class labels remain analytical labels, not Skill identities. The versioned
  artifact now binds each class to an explicit `tool:<id>` or `skill:<id>` capability
  plus required arguments. Runtime validates that binding against the live owner and
  read effect; the only enabled class now produces a direct refund-status Tool command.
- Training output, checked artifact metadata, runtime validation, route compilation,
  public ASGI/PostgreSQL behavior and affected resume tests were migrated together.
  The expanded Target and affected-consumer suite passed 186 tests with PostgreSQL
  enabled.

## Stage 26 verification notes

- LangGraph remains the parent scheduler. A new thin Worker adapter invokes the
  existing `BaseAgent`/`ReActExecutionEngine` selected from `AgentOrchestrator` only
  for `DELEGATED` WorkItems; direct Tool and governed Flow paths remain unchanged.
- `MCPToolManager` now owns the intersection between an Agent allowlist and the
  current WorkItem Tool envelope at both discovery and execution. Invalid envelopes
  fail closed, and a model-generated call outside the envelope is denied even if the
  Agent normally owns that Tool.
- ReAct returns internal structured Tool results alongside receipts. The adapter
  creates facts only from successful Tool authority metadata matching a declared
  requirement; Agent prose cannot manufacture an authoritative business fact.
- A pinned composite Skill executes through its registered Skill executor without a
  second Agent planning call. Unpinned delegated work retains the existing bounded
  ReAct loop and its dynamic ordering of permitted read Tools.
- Focused ReAct, Tool security, planning, LangGraph, Product and Target HTTP suites
  pass 71 tests. The full repository run reached
  1,191 passes; its eight failures are pre-existing cross-consumer inconsistencies in
  the separate uncommitted RAG policy work and stale legacy account-authority tests,
  not failures on this Stage 26 causal surface.

## Stage 27 verification notes

- The existing ReAct loop now accepts host-provided composite capabilities beside
  atomic Tool schemas. These capabilities are read-only and invocation-scoped; they
  do not enter Tool Registry identity, do not survive as hidden workflow state, and
  cannot replace a high-risk Flow.
- `TargetAgentExecutor` projects only Registry-allowed Skills that have a concrete
  executor. The Agent may choose one dynamically, while a `RUN_SKILL` fast path still
  invokes a pinned Skill directly without spending another model turn.
- Composite Skill arguments are validated against the Registry contract. The Skill
  receives only its own Tool subset, and its internal Tool calls continue through
  `MCPToolManager`, preserving schema, identity, audit and authority enforcement.
- A general compound Product request now defers to structured understanding and
  compiles one `DELEGATED` Product WorkItem. It is not split into category-specific
  Skills and does not trigger Multi-Agent fan-out merely because it needs multiple
  capabilities.
- An integration test drives the real `TechnicalAgent` and `ReActExecutionEngine`
  through dynamic `product_identification` Skill selection, then an atomic Knowledge
  Tool, and verifies both authoritative requirements in one `AgentResult`. The
  expanded focused suite passes 90 tests with PostgreSQL-enabled HTTP coverage.

## Stage 28 verification notes

- `TargetConversationManager` now loads a bounded current-thread context projection
  before understanding and passes it to workers after planning. This is separate from
  cross-session retrieval and does not replace the append-only conversation record.
- A historical-reference signal that deterministic state cannot resolve triggers
  exactly one authenticated `service_episode_search` with
  `REFERENCE_RESOLUTION`. No signal means zero cross-session retrieval, and there is
  no Memory → Router → Memory loop.
- Retrieved episode identities and provenance are supplied to structured understanding
  as untrusted evidence data and retained as evidence references. Provider instructions
  explicitly deny Memory and Media content any instruction authority.
- The existing ServiceEpisode Tool is now present in the Target Registry and relevant
  domain Agent allowlists. It remains an atomic Tool rather than a pseudo Skill.
  Delegated WorkItem Tool envelopes are derived from declared requirements and Skill
  contracts, so merely belonging to a domain does not expose every domain Tool.
- Context, routing, persistence, planning, ReAct and Target HTTP focused suites pass;
  the lifespan suite is currently blocked before Target composition by the separate
  uncommitted RAG policy adding three keys without migrating `AgentBundle` validation.

## Stage 29 verification notes

- Media is a shared evidence capability rather than a Product-category taxonomy.
  `media_read` provides deterministic L1 text extraction; `media_observe` provides
  task-conditioned L2 visual observations grounded by asset checksum, locator,
  producer and version. Both remain atomic governed Tools.
- The default Registry exposes these Tools to the relevant domain Agents while the
  accepted WorkItem envelope determines what a particular invocation can discover
  and execute. No per-product, installation or attribute-specific Skill was added;
  the bundle still contains only the reusable composite `product_identification`.
- Asset presence alone no longer implies Product identification. Explicit OCR/text
  goals compile to one direct Tool call, explicit visual-reasoning goals delegate to
  one bounded Agent, and explicit Product-model identification may use the composite
  Product Skill. A genuine refund-plus-model request retains both independent tasks.
- `media.visual_observation` is now an authority requirement backed only by the
  registered media evidence adapter. Media observations cannot satisfy current
  order, refund, account or other business-state requirements.
- Media contracts, routing, Product runtime, Tool governance and real
  ASGI/PostgreSQL Target scenarios pass 97 focused tests.

## Stage 30 verification notes

- `ActionDefinition` now owns whether a business write can be interrupted by an
  account-security task. Refund creation, order cancellation and shipping-address
  change are marked interruptible; account freeze and human handoff are not.
- `RoutePolicy` applies this Registry fact before WorkPlan compilation. When the same
  turn contains an account-security task, interruptible business writes are omitted
  before any preparation Tool runs or Flow is started. Read-only tasks and essential
  human coordination remain available.
- The resulting plan contains no hidden cancelled Worker and performs no speculative
  write preparation. Publication states that the safety task was prioritized and that
  the other high-risk operation was not started, so the user can request it again
  after resolving the security concern.
- The policy is capability-scoped rather than a global shutdown and does not add
  routing rules to any domain Agent. Planning, Registry, structured routing and real
  ASGI/PostgreSQL Target tests pass 36 focused tests.

## Stage 31 verification notes

- A checkpointed parent graph now stops at a native LangGraph `interrupt()` after a
  typed `NEEDS_USER_INPUT` outcome or successful governed write preparation. The
  interrupt payload contains only interaction shape and the bound WorkPlan
  fingerprint; it does not become the approval or business-state authority.
- ConversationState persists the exact checkpoint thread beside PendingInteraction or
  PendingApproval. On the next turn, DeterministicResolver first consumes the typed
  business signal with CAS; only then does ConversationManager pass the newly
  Registry-validated WorkPlan through `Command(resume=...)` to that same graph thread.
- Resume replaces the old terminal/needs-input board before dispatch, so successful
  work is not duplicated and the previously blocked WorkItem runs once with its new
  arguments. Approval rejection or expiry resumes the graph with an explicit cancel
  signal and executes no write WorkItem.
- LangGraph still owns execution position only. Conversation state, consumed signal,
  accepted approval, operation key, Receipt and reconciliation remain in their
  existing business owners. Native graph, Manager, HTTP and PostgreSQL suites pass 26
  tests, including same-thread approval continuation.

## Completion audit

- The repeated architecture reopenings had one shared cause: execution-shape meaning
  was being encoded at the wrong layer. Asset presence was treated as a Product task,
  atomic Tools were treated as mandatory Skills, and domain ownership was treated as
  mandatory Agent dispatch. The converged owners are now explicit: Understanding
  proposes goals, Registry owns capability/risk, RoutePolicy accepts and preempts,
  WorkItem owns execution shape, and LangGraph only schedules and resumes it.
- Fresh adversarial coverage iterates every Registry Action marked
  `interruptible_by_security`; all are removed before WorkPlan/Flow creation when an
  account-security task is present. The test does not special-case refund.
- The final PostgreSQL-enabled repository run passed 1211 tests after native resume.
  Six exact tests were deselected because a separate uncommitted RAG change emits
  three new retrieval-policy keys while `services.evolution.bundle.AgentBundle`
  still rejects them. Those six share that external owner mismatch; no Target test or
  affected Target consumer was excluded.
- Two stale legacy assertions were migrated: `account.current_state` is now supported
  but requires evidence, and account-state requests route to a domain owner rather
  than mandatory handoff. This aligns tests with the authoritative Registry instead
  of weakening the implemented account-security capability.
