# Prepared action dialogue and completion convergence

Status: owner-level changes implemented; original two regression tasks verified, broader interaction convergence partially verified and not closed. Repeated-reopen protocol active: approval/question duplication and post-write completion have repeatedly resurfaced. This is a bounded architecture-first review, not another task-specific patch.

## Observed witnesses

- New τ³ tasks 2/3 first request confirmation via domain request_user_input, then create PendingApproval and ask again after the user's explicit yes.
- Task 2 commits the return once, resumes domain objectives, then report_blocked("already completed; no further action") leaves task_completed=false.
- Source evidence: artifacts/eval/tau3-new-dev2-2026-09-07-attribution-v1, commit e3f5039. Preserve scores and original trajectories.

## Supported target contract

Missing user information is distinct from approval of a prepared action. The action preparation owner produces the exact action and approval binding; one pending decision owns the approval question. A committed action receipt is authoritative for that action, not blanket proof of every delegated objective. Follow-up work retains only genuinely remaining objectives; completion and blocked outcomes must have unambiguous evidence and lifecycle semantics. Independent goals, rejected/edited approvals, unknown writes, cancellations and persisted resumes retain their own outcomes.

## Plan

1. complete — trace relevant producers/consumers: domain tool loop, prepared actions, interaction assembly, pending state, resume compilation, result board, recovery, persistence and final completion projection. Compare with current official framework HITL practices.
2. complete — document shared causal model, owner-level contract, smallest coherent change and migration/non-goals before edits.
3. complete — implement and migrate affected consumers/tests/docs; retain SDK loop, no alternative runtime, no benchmark-specific IDs or semantic text heuristics.
4. in_progress — property/state-transition tests plus representative PostgreSQL integration; independent fresh-context review and fresh adversarial checks before closure.
5. pending — report verified boundaries, remaining uncertainty, commit and push scoped files.

Non-goals: alter official scoring, replace benchmark judge, add business-specific confirmation keywords, force every domain objective to succeed after one write, or redesign unrelated RAG/telemetry.

## Causal review and chosen change

Cloud execute_work_item inputs prove task 2's count-only objective inherited every retail action, then prepared the return requested in the full current message. The legitimate return objective was resumed after another objective's write. This is task-envelope overbreadth, not simply an unlucky completion word. RoutePolicy currently grants all owned actions for every open delegation. Plan outputs must distinguish investigation-only vs action-proposing goals; default read-only, preserved on resume, with Registry still limiting eligible action definitions. This is coarse task intent, not a tool sequence or authorization grant.

Preparation descriptions currently concatenate the raw write tool's execution confirmation precondition, although preparation must precede confirmation. Remove that semantic collision at the wrapper owner, retain argument schema and authoritative business checks. Requesting missing input remains allowed; no lexical filter or retroactive approval inference.

Resume currently copies old unresolved ToolMessages and supplies new facts separately. At the framework context conversion boundary, resolve only the bound pending tool interaction: a user reply completes the matching input request; a committed receipt completes only the proposal with the same operation_key. Preserve original persisted result as history and project the current tool result using SDK messages. Do not overwrite unrelated calls, invent receipts, or equate all objectives with a committed action. Explicit resolved-tool evidence and scoped objectives let normal no-tool-call completion remain the SDK exit; do not add another completion tool/LLM router.

Official reference checked 2026-09-07: https://docs.langchain.com/oss/python/langchain/human-in-the-loop and https://docs.langchain.com/oss/python/langgraph/interrupts. Mature pattern binds an approval/respond decision to the paused tool and resumes with its result. Reuse current LangGraph execution and business approval ledger; replacing the entire parent graph with a second HITL owner would duplicate existing authority. Current installed 1.2.11 does not support all newer documented conditional/respond conveniences; no dependency upgrade needed for message projection.

## Implemented boundary and acceptance evidence

- Planning declares `allow_action_proposals` per open goal; it is not approval. Model output must explicitly provide it; internal commands default to false. Registry ownership and runtime approval still constrain every write. No per-business tool sequence is encoded.
- State-bound continuation carries its original WorkItem capability envelope. Independent review exposed that inferring this from skills/action availability lost non-skill read tools. Restore the explicit envelope after registry, owner and control-version validation instead.
- Preparation wrappers describe preparation, not the raw write API's pre-confirmation instructions. Domain context treats other topics as context rather than additional objectives.
- The framework message adapter projects only the originating pending ToolMessage. Trusted consumed input yields ANSWERED (not approval); a matching committed operation receipt yields COMMITTED. SDK call/message identity is preserved; archived originals are unchanged. Unknown/failed/unrelated receipts never resolve the proposal.
- Existing SDK completion, ResultBoard and Publication owners remain unchanged. A committed action does not automatically complete remaining objectives or convert BLOCKED to success.

Latest scoped validation: 198 tests passed using real PostgreSQL, including repeated input/approval round trips, process restart, read-only scope preservation, inline/archived artifact identity and operation/owner/requirement mismatch cases. Nine existing warnings concern nested trusted-context serialization and multiprocessing fork; no new warning suppression. This does not prove fresh model behavior.

Exit checks still pending: independent final review and a single fresh run of original development tasks 2/3, explicitly checking confirmation count, committed writes, truthful public reply and internal completion. Preserve original runs and unavailable official judge status. Do not claim closure from pytest counts or adjust ACTION reference matching.

First fresh verification at 0256231 did not close the task. See `artifacts/eval/tau3-action-dialogue-dev2-2026-09-07-v1/REPORT.md`: task 2 ENV=0, task 3 eventual ENV=1 after an incorrect unavailable response. The model omitted the optional scope, and its capability cards omitted action proposals. This is an input/output contract gap, not a new business exception. Action cards now derive from Registry; model scope is mandatory and absent scope is a typed invalid plan. Added model-facing card → plan → RoutePolicy tests and schema omission tests. Follow-up targeted tests: 97 passed. Both attempts and limitations must remain in the final report.

Second verification at aa39569: both ENV=1 and internal completion true, but both mixed missing-input questions still reconfirm the known target. Remaining contract gap: hint prose was treated as mandatory input meaning by the composer/verifier. Scope-preserving input hints must express only genuine gaps; public wording and verification use the actual unresolved choice rather than copying redundant permission. Existing model calls now get the current interaction purpose, with ordinary choices/real ambiguity distinguished from execution approval. No extra model, keyword classifier, output envelope or runtime is introduced. Pure permission-only input is not silently consumed by dropping its question. Candidate component results still include a false rejection of adequate approval wording, explicitly retained in the v2 report; no closure assertion.

Latest full scoped PostgreSQL suite: 215 passed, nine existing warnings. Reply/verifier-focused suite after wording changes: 145 passed. These scripted tests validate interfaces/revision mechanics, not semantic model accuracy. Final end-to-end run will use the frozen candidate and retain every attempt.

Third frozen E2E run at d8458f8: both ENV=1, single formal approval and write, truthful final reply, SUCCEEDED/SUCCEEDED and task_completed=true. Full evidence in `artifacts/eval/tau3-action-dialogue-dev2-2026-09-07-v3/REPORT.md`. Scope remains partial rather than closed: component approval-verifier false positives and recovery from a pure-permission invalid pending input remain unresolved. No more prompt-only counterexample iterations are counted as closure. Dependent claims of complete dialogue convergence remain blocked by those explicit acceptance gaps; original two regression outcomes are reported separately.

## Active continuation: repair the interaction owner, then ten fixed tasks

Previous goal turn classified as progress: committed owner changes and retained three real regression runs. Full objective remains: resolve current gaps, run ten tasks, audit outcomes and repair shared causes without business-specific branches.

Independent source review confirms `execute_work_plan → commit_turn_state → assemble_response`. Invalid input is therefore already persisted before semantic validation; replay only regenerates prose. Chosen positive contract:

- Existing verification distinguishes invalid bound input candidates from ordinary answer revision and provider failure; identities must match the runtime's input set. No parsing feedback prose to decide control flow.
- TurnGraph becomes execution → commit execution progress → assembly/interaction validation → commit new input. Completed actions and prepared approvals remain durable even when wording fails; only a new missing-input candidate is provisional. One checkpointed repair may return rejected inputs to their originating domain goal. Provider outages remain assembly retries, never business re-execution.
- Manager owns a state-bound repair plan, with original capability/control constraints. Provisional invalid waits are not committed; internal rejection never consumes a user signal or approval.
- Orchestration's existing resume preserves exactly unchanged independent WorkItems/results and recomputes changed dependencies. Only affected domain objectives rerun. Receipt-backed successful writes cannot repeat.
- SDK ToolMessage projection marks the original input request internally REJECTED, not ANSWERED; archives remain immutable. Budget exhaustion is a typed failure with no newly committed invalid wait.
- Old in-flight TurnGraph contracts are explicitly version-rejected, not executed through a fallback runtime. No opaque checkpoint migration.

Next milestones (not closed): (1) state/contract migration and property + PostgreSQL restart tests; (2) frozen component counterexamples including genuine choice, ambiguous target, pure permission, adequate/inadequate approval; (3) fixed train task range 4–13, ten tasks, seed 300, no substitution or best-of reporting; (4) per-task execution/publication/state/score attribution, shared-root repair, fresh revalidation and independent review. Official judge unavailability must remain separate from business failure. The ten-task stage must not begin merely because the two original cases passed.

### Repair implementation and review evidence

- One semantic assessment now returns bound rejected input identities separately from ordinary wording feedback. Invalid input invokes domain reconsideration; ordinary wording gets the existing single composition revision. No new planner, approval model, or business-specific branch.
- Checkpoint roundtrip changed WorkItem tuple fields to lists, defeating unchanged-work equality. WorkItem now normalizes its immutable sequence contract at construction; recovery preserves exact unchanged results and their shared facts.
- Independent review found that delaying all state commits would also delay committed write completion, and that the no-work branch could lose the committed prefix. Progress commits now include both paths. A real governed-write regression covers COMMITTED → two invalid follow-ups → reopen: one write, completed workstream, no fabricated answer/approval or invalid pending interaction.
- Three pre-existing TurnRuntime assertions expected undated current-state prose. Replaying HEAD's original TurnRuntime reproduced all three failures; assertions now bind to the actual receipt observation timestamp. Production rendering was not changed.
- Component v4 uses a new bound-input fixture; historical v1–v3 inputs/results remain unchanged. Four valid cases pass; the pure-permission case is rejected with its exact task identity. This is five synthetic component cases, not a customer success rate or fresh ten-task closure.
- Remaining acceptance: final scoped suite, independent re-review, then fixed business E2E. No closed status inferred from test count or a single semantic replay.

Final scoped suite after owner fixes: **277 passed**, PostgreSQL enabled, 12 existing trusted-context serialization warnings retained. Independent final scoped source review found no new concrete blocker; its local subset was 26 passed/3 database-skipped. Implementation is ready for original task 2–3 regression, not globally closed and not yet a ten-task report.

### Original-task v4 falsifies global closure

At 50c3201, task2 ENV=0/no return, task3 ENV=1/one modification but intermediate verifier error and extra confirmation. See `artifacts/eval/tau3-action-dialogue-dev2-2026-09-07-v4/REPORT.md`. Ten new tasks remain held, not started.

Langfuse proves correct initial separation: count objective with proposals disabled; return objective with proposals enabled. Both same-owner workers instead acted on the full user message. Count emitted the return input tool; return emitted a plain question and was considered SUCCEEDED because requirements were empty. Only the count objective resumed. The response verifier receives result status and facts but not the assigned objective, so it cannot verify the question's goal binding or distinguish goal completion from natural model termination. Later `task_completed` covers the reduced current plan, not original unresolved work.

Reopen protocol remains active: review the whole relevant goal authority surface before further production edits. Required positive contract must separate model/step termination, evidence coverage, objective fulfillment, and customer reply adequacy; preserve original unresolved objectives across resumes. No new return-specific branch, keyword-based grant, success coercion, or prompt-only closure. Existing input repair/progress commit work is retained as infrastructure, not evidence that the global defect is closed.
