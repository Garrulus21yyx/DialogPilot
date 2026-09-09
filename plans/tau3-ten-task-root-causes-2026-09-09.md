# Ten-task causal audit — 2026-09-09

Status: implementation/structural verification in progress; semantic closure OPEN.
Entry plan: `conversation-direct-capability-convergence-2026-09-08.md`.
Sources: `artifacts/eval/tau3-new10-after-encoder-trial-2026-09-08/` and
`artifacts/eval/tau3-fixed10-repair-validation-2026-09-09/`.
No new simulator or paid model run is part of this repair.

## Causal groups, not a business-case patch list

| Root | Evidence / mechanism | Authority and repair | Remaining proof |
|---|---|---|---|
| R1 capability protocol mismatch | Original log repeatedly raises `planning_action_unavailable`; 19/29 have no executed tools | Main catalog now exposes registered native READ schemas, SDK calls compile to observed reads, same original-request planner continues. Already implemented before this audit. | New real-task results are not implied by catalog tests. |
| R2 misassigned goal/envelope has no owner correction | 28 assigns authentication/status only; domain reviewer checks only that assignment; local retries cannot alter allowed_actions | Existing handback reviewer can report a typed assignment issue with sibling/retained scope and registry capabilities. Nonretryable local attempt returns to existing observation/planning, not another router. Normal delegated outcomes do not replan. | Reviewer detection accuracy remains semantic, not proven by scripted assessments. |
| R3 proposal decision conflated with goal cancellation | Decline/expiry formerly cancel origin/dependency DAG; accepted review used rejection budget | ceaa739 separates exact proposal decisions, explicit goal revision/cancel, and rejection budget; current audit also migrates stale catalog wording and runner metric. | Native model confirmation behavior still requires separate evidence. |
| R4 cross-turn argument authority duplicated | Replay22 complete address rejected because current message omitted prior address text | a519962 removes generic delegation's duplicate literal-address gate. Domain tools own assembled parameters; direct shortcut remains explicitly bounded. | No fresh tau replay after fix. |
| R5 context admission differs by consumer | Replay21 actor input fits, reviewer input overflows | ae1504d shared actor/reviewer admission accounts for actual review envelope. | No real model quality claim from context tests. |
| R6 semantic action feasibility false acceptance | Replay19 review ec0a89a98d1d8bdc sees full objective + policy and accepts exchange-first; 06783c561cf5e61e recommends that order | Actual model judgment error, not missing checkpoint state. Existing preparation/runtime check individual authorization, not arbitrary natural-language future-plan satisfiability. No case-specific order rule added. | OPEN: actual semantic feasibility improvement not established. |
| R7 final prose false acceptance | Replay22 c85fc4e9b25972cf includes internal drafting prose and explicit prohibition; returns supported/answered true | Model false positive, not proven SDK reasoning-field leakage. Existing author/revision/verifier preserve exact candidate. No regex deletion or fake success. | OPEN: actual generation/verification improvement not established. |
| R8 runner lifecycle / score attribution | Replay exits143 with stale RUNNING; task30 max_steps triggers official early return before ENV evaluation | Shield/join synchronous simulator, stop bridge, deliver last known tool result and drain actual turn, then close resources. Signals record STOPPING/INTERRUPTED and partial records. Label official termination gate separately. | Cooperative stop only; SIGKILL cannot guarantee finally. Signal origin of old run remains unknown. |
| R9 checkpoint revision selection differs by entry | New assignment-repair test succeeds but old failed revision keeps request incomplete | Fresh observation graph and resumed graph now share `_unreplaced_outcomes`; original checkpoint stays audit record, effective result scope uses current revision. | Property/integration checks cover correction, failure, waits, replay. |

## Every original task and available replay

| Task | Original / replay evidence | Root attribution |
|---|---|---|
| 19 | Original identity phase repeatedly protocol-rejected, no effects. Replay exchanges commit, subsequent return refused due changed order state. | Original R1; replay R6, plus redundant confirmation/prose R3/R7. Not a lost second WorkItem. |
| 20 | Both runs change four items and pay $71.96; original still contains early protocol failures. | Early R1; successful batch is counterevidence to blanket "multi-task unsupported". |
| 21 | Modification commits; ACTION misses reference calculate. Replay also has reviewer budget/format failures. | R1/R5; missing reference call is action-trajectory mismatch, not failed modification. Judge dependency absence is environmental. |
| 22 | Original protocol errors prevent address change. Replay rejects cross-turn address and user withdraws. | R1/R4; final drafting prose R7. Withdrawal must remain no-write, not be overridden. |
| 23 | Original lookup succeeds after protocol errors, then claims records not refreshed and does not act. Replay has only partial evidence: helmet work, not all goals completed. | R1/R2 candidate mismatch; replay complete-goal check rejects premature completion. No proof all three goals were lost from state. |
| 24 | User chooses to keep order; queries complete, no cancellation expected. | Early R1; no-write is appropriate. Missing ALL judge is not business failure. |
| 25 | Queries only, later return/handoff requests fail or get limitation; all scores nevertheless1 because reference actions are reads. | R1 and request-coverage gap R2. Scoring inadequacy is separate from actual fulfillment. |
| 28 | Protocol failures, then explicitly narrowed authentication/status assignment and no requested returns. | R1/R2. Local completion cannot attest whole-request fulfillment. |
| 29 | Protocol rejection from identity turn; no real tools; eventual mutual waiting. | R1 established. Do not invent a refund/exchange-specific cause without execution. |
| 30 | Return/cancel tools appear in trajectory; repeated confirmations consume rounds; max_steps terminates. | R1/R3 contribute rounds; R8 official gate bypasses actual ENV check. Tool call alone is not receipt/database closure. |

Only replay19–22 have completed result rows. Replay23 is partial; remaining tasks
have no replay outcome. Do not turn absence into a zero or use original scores as
new regression evidence. Original signal source cannot be reconstructed from exit143.

## Positive repair contract

User request is source authority; planner assignments are scoped interpretations.
The domain may correct its candidate, but not expand its own envelope. A detected
assignment error returns to the existing planner with the original request, sibling
scope, tool evidence and exact feedback. It never retries the invalid envelope via
another task's approval. Unchanged failures remain bounded by existing progress and
planning budgets. Explicit replacement consumes the old revision; independent
outcomes and pending approvals/inputs survive. An unavailable planner produces
partial delivery with the uncompleted issue retained, not fabricated completion.

The reviewer performs this classification in its existing call. No extra mandatory
LLM router/reviewer, new task engine, new storage service, or task-ID branch is added.
Tests with scripted reviewers prove handling of a decision, not that the real model
will always make the right decision. R6/R7 therefore cannot be marked repaired merely
because typed feedback, context or pipeline tests pass.

## Verification record

570 local/component/PostgreSQL tests passed after the structural changes, including
planner-unavailable partial results, checkpoint/artifact roundtrip, concurrent
approval/input/queued tasks and no-progress termination. One pre-existing fork
warning remains. 19 bridge/scoring tests passed in the installed tau environment,
including queued final-result delivery before aclose, actual async cleanup, thread
join and official termination-gate labelling. These tests do not invoke the user
simulator/model and do not produce new official task scores. Independent reviewers
checked assignment and shutdown owners separately; semantic R6/R7 remain OPEN.
