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
| 11 | done | Real PostgreSQL/HTTP six-scenario E2E, async checkpoint ownership, committed handoff workflow completion, and documentation convergence | 68 Target tests pass against PostgreSQL; repository suite 1120 passed / 6 unrelated dirty-RAG contract failures | this stage commit |
| 12 | in progress | Refund approval/resume/write/reconciliation public lifecycle | contract audit complete; implementation pending | stage-12 contract commit |

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

## Scope correction after executable-core review

The original eight-stage list ended at in-memory E2E. That is not sufficient to
claim migration completion because the public API still owns the old runtime path.
Stages 9-11 were added to cover the actual remaining causal surface: durable owners,
API cutover, removal of duplicated authorities, and real boundary E2E.

## Bounded v1 omissions after stage 11

- Refund request remains eligibility-only at the public API. No approval grant is
  synthesized and `refund_request_create` is asserted absent. Approval/resume/write/
  reconciliation is a subsequent capability stage, not part of the stage-11 claim.
- Product media/catalog orchestration is boundary-tested with a governed tool double;
  production registrations for `media_read` and `catalog_search` remain pending.
- The public understanding path is deterministic and bounded. The evaluated encoder
  policy contract exists, but no production encoder/Structured LLM fallback is claimed.
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

The current code has the state primitives but not the complete conversion
boundary: `ChatRequest` carries no approval signal, `START_WORKFLOW` always emits a
new START mutation, and `PendingApprovalState` does not retain target-version data
needed to rebuild the original write contract. Connecting the write executor now
would therefore create either an unbound grant or a second operation. Stage 12
must migrate these contracts together rather than special-case the refund text.
