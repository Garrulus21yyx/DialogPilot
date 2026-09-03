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
| 1 | done | New registry, work-item, agent-result, fact/evidence, and interaction contracts | 8 contract tests | stage-1 contract commit |
| 2 | pending | Conversation state, workstreams, pending interaction, and deterministic resolver with CAS semantics | state-machine/property tests | pending |
| 3 | pending | Turn policy/compiler producing direct, delegated, multi-domain, and workflow plans | compiler/policy tests | pending |
| 4 | pending | Parent orchestration graph, direct runtime, delegated domain workers, result board, and partial failure | orchestration integration tests | pending |
| 5 | pending | Governed write workflows, approval binding, operation keys, receipts, and reconciliation | write state-machine tests | pending |
| 6 | pending | Handoff commit/receipt and single publication/delivery path | handoff and delivery tests | pending |
| 7 | pending | Encoder fast path and memory/media evidence routing | accepted-precision and binding tests | pending |
| 8 | pending | Six vertical E2E scenarios, capability-scoped safety gates, docs, and measured report | full suite and held-out cases | pending |

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
