# Approval and public output convergence

Status: implementation and bounded contract verification complete. Live-model task22
and general conversational quality are NOT declared closed.
Base: 0137db8. Preserve unrelated dirty files. No paid benchmark in this work.

Evidence: task22 single rerun rejected review_action's response/decision/batch
combination; three replies failed, no writes. First reply exposed drafting prose.
The exact raw approval combination is not retained locally. Do not invent it.

Causal model: optional response is advertised by tool schema but converter allows
only hold-alone. Meanwhile free model text has two roles (working commentary and
public answer); the no-call branch implicitly publishes it. Scripted tests omit
these cross-products. These are contract/conversion and verification defects,
not a reason to add another reviewer or recreate approval/execution state.

Review scope: native actions/provider -> ConversationAgent -> RoutePolicy/compiler
-> Manager approval/resume -> Runtime/ResultBoard -> response assembly -> Publication
and checkpoint/transcript consumers, including failure/cancellation paths.

Target: approval decision binds only the unchanged prepared operation. Independent
work can coexist; prose never changes authorization or bypasses execution results.
An explicit public-answer channel distinguishes final text from planning preamble.
Only a waiting/conversation turn may publish a planning-time answer; execution
turns obtain final wording from their outcomes. SDK still parses messages/tools.
No phrase blacklist, extra online judge, provider switch, new runtime, or business
case branch. Existing typed invalid/duplicate/unknown-action handling remains.

Steps:
1. done: trace owners/consumers and reference practices.
2. done: implement one coherent action/output contract and migrate consumers.
3. done: generated/permuted algebra, complete manager/publication and SDK tests.
4. done: independent fresh-context review; no production blocker, migration
   requirement incorporated below. Scoped commit/push identified in handoff.

References read 2026-09-09 (engineering guidance, not a SOTA claim):
- https://docs.langchain.com/oss/python/langchain/human-in-the-loop : decisions
  address concrete interrupted calls; approval is not execution success.
- https://docs.langchain.com/oss/python/langchain/messages : SDK separates text,
  reasoning, tool calls and artifacts; plain-text semantics remain model-generated.

## Implemented bounded contract

| Native batch | Compiler input / public behavior |
| --- | --- |
| respond(response) | RESPOND, no work or mutation |
| review_action(hold) + respond | RESPOND, approval remains unchanged |
| approve/decline + optional respond | RESOLVED, exact decision; outcome author writes final reply |
| work/input + any valid decision + optional respond | RESOLVED, all work/input retained; preliminary text stays private |
| hold alone / no call / duplicate terminal / unknown call | Typed invalid output, no execution |
| approve + revise/cancel same target | Existing approval owner rejects conflicting authorization |

review_action has only decision; respond has only response. No additional nesting,
fact-ID annotation or model judge. Tool text does not decide business status.
Full-batch validation remains before execution. Six existing work/action slots
remain available with response and read-binding metadata; no work is silently cut.

## End-to-end owner review

- Native SDK: normalizes provider thinking/text/tool calls and parses arguments.
  Required `any` tool selection uses existing SDK. Thinking-enabled integrations
  drop forced selection: tested with real SDK mock HTTP; no custom workaround.
  A resulting no-call response remains a typed failure, not a fallback reply.
- conversation_actions: sole wire-to-proposal conversion. Free text has no public
  meaning; only respond's explicit field can become response_text. Decisions and
  independent actions are reduced as a complete batch, order-independently.
- ConversationAgent/RoutePolicy/compiler: existing RESPOND vs RESOLVED contracts
  are sufficient; no new flag, reply state, or permissive downstream exception.
- Manager/approval owner: exact identity, arguments, revision, consume-once and
  unchanged/revised continuation remain authoritative. Observation does not
  consume the user's already handled decision again. Hold preserves the wait.
- Runtime/ResultBoard: receipts, faults, partial outcomes and waits determine
  execution status; planning prose cannot replace execution or its outcome.
- ResponseAssembler: direct explicit reply uses existing validation; ordinary
  bound domain questions keep their existing no-author/no-judge path. Domain
  final AIMessage text remains internal domain_notes for composition, not an
  implicit public candidate. No blanket deletion of domain working messages.
- Composer: same flat respond channel, one model call; surrounding text/thinking
  not returned. Existing callback parent is preserved with framework merge_configs.
- Publication/Transcript/Delivery: consume the selected text and existing verified
  evidence/bindings, preserve idempotent replay. Simulator consumes Publication,
  not model text; no simulator parser or message filtering was added.

## Persistence and migration

Provider version v25; TurnRuntime version v19. The latter is necessary because a
completed but unpublished v18 checkpoint bypasses providers and returns saved
response_text/assembled. Those records cannot attest the new explicit channel.
Existing typed version rejection prevents new publication or automatic business
re-execution. Deliberate tradeoff: all unpublished v18 turn checkpoints require
explicit reconciliation, including otherwise safe ones. Already published replies
retain original replay; new turns and business Workstream/Receipt formats remain
unchanged. No second live parser or automatic old-runtime fallback.

## Verification and limits

Initial targeted set: 444 passed. Expanded real-PostgreSQL set first had four
failures: stale assertions expected a removed question author, a removed ordinary
domain judge (two cases), and a truncated exception chain. Fixtures now assert
the existing no-extra-model behavior and preserve the complete typed cause chain;
no production branch was added to satisfy them.
Expanded rerun before the final version witness: 651 passed, 9 SDK warnings.
Independent fresh-context reviewer: 353 passed, 2 PostgreSQL cases deselected
(overlaps main run, not additive). Reviewed batch algebra, consumers, work capacity,
plain-text publication and checkpoint bypass, with no remaining production blocker.

New adversarial tests include drafting prose plus SDK reasoning/text/tool blocks,
both author stages, batch permutations, all decisions with input/read/delegation,
six reads plus metadata and public text, current input observation, and native
planning through PostgreSQL reopening and Publication. HTTP tests now route
approval+acknowledgement and composition through the native SDK adapter, retaining
existing workflow writes, receipts, decline, stale/foreign signals and replay checks.

This proves channel/transition invariants, not live model instruction compliance.
The explicit response field could still contain poorly authored text; no regex or
extra online judge can establish universal semantic correctness. Raw preamble is
no longer automatically published. No paid model calls or tau rerun in this repair.

Final verification on the delivered implementation:
- 655 passed, 9 warnings, 55.18s, real local PostgreSQL enabled through
  TEST_DATABASE_URL (isolated test database created/removed by existing fixtures).
- 19 test modules: public_action_algebra, conversation_actions,
  current_approval_reply, compound_approval_planning, conversation_response_contract,
  approval_observation_continuity, target_http_postgres_e2e, target_chat_cutover,
  approval_conversation, approval_revision_lifecycle, domain_action_approval,
  turn_runtime, native_response_contract, conversation_planning_schema,
  structured_model_transport, langfuse_framework, planning_current_turn,
  conversation_agent, dynamic_knowledge_filters (all prefixed tests/test_).
- Additional targeted recovery/SDK run: 98 passed (overlaps, not additive).
- `git diff --check` clean. Existing model integration warnings cover forced
  tool choice with thinking, not failed test calls. No network LLM requests.
- Fresh-context review approved the owner-level change and required v19 checkpoint
  invalidation. New witness proves an unpublished implicit reply cannot publish,
  re-author or re-execute after upgrade. Current-version PostgreSQL recovery and
  published-response replay remain passing.
- Three production files changed; tests and this report migrate their consumers.
  Unrelated framework-worker/RAG/archive/documentation changes remain unstaged.

Live validation now FAILED: the requested task22 run on305e001 completed with
ENV/ACTION/ALL=0, no address writes and repeated planning_requires_action errors.
The parsed live model output often had no native action; the public-channel
contract therefore rejected ordinary turns and composition. Approval was never
reached. Unit/SDK fixture success does not establish live-provider compatibility.
See plans/tau3-task22-public-channel-rerun-2026-09-09.md for evidence and uncertainty.
Closure remains open; inspect actual SDK/provider transport before another repair.
