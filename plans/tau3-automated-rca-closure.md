# Tau3 automated RCA closure

Status: task20 read-replay root cause established; owner-level repair and fresh validation open

Goal: turn saved tau3 evaluations into an automated, evidence-backed loop that
analyzes failures, runs deterministic probes, creates reviewable regression
candidates, compares a candidate run with a baseline, gates regressions, and can
publish compact scores to the existing Langfuse trace lineage.

Constraints:

- Work on `feat/customer-service-target-architecture`; the earlier isolated RCA
  branch has already been integrated into this feature branch.
- Keep objective violations, immediate mechanisms, hypotheses, and verified root
  causes distinct.
- Never attach an unkeyed batch log error to a task.
- Never promote a generated regression candidate without a reviewed contract.
- Offline analysis and gates must work without model or Langfuse credentials.

Steps:

1. `done` Define closed schemas for evidence, probes, regression records,
   comparisons, and gate outcomes.
2. `done` Implement deterministic probes and explicit hypothesis promotion.
3. `done` Implement regression candidate generation and reviewed promotion.
4. `done` Implement baseline comparison and CI gate policy.
5. `done` Implement optional Langfuse evidence retrieval and score publication using
   existing session IDs.
6. `done` Add CLI orchestration, tests, documentation, and real-artifact checks.
7. `done` Add a bounded semantic evidence slice and structured LLM Judge;
   judge output may support/refute hypotheses but cannot verify a root cause.
8. `done` Reconcile causal metadata with the updated runtime's authoritative
   control revisions and document the remaining owner instrumentation boundary.
9. `done` Rebase onto the complete planning-request budget owner, preserve the
   terminal projection report at that boundary, and propagate its typed evidence
   through planning, trace, and RCA without a second admission authority.
10. `done` Re-run task20 and add termination-gate/read-replay attribution from
    authoritative trajectory and pending-interaction evidence.
11. `done` Trace repeated reads through parent planning, observation publication,
    delegated worker input, continuation persistence, compaction, and progress
    enforcement. The first repeated product batch occurred in one retail work item
    immediately after `context_summary`, not at a continuation boundary. The shared
    invariant gap is that completed read identity/freshness is advisory model context:
    compaction preserves archive navigation, but the tool boundary neither resolves an
    unchanged read to that immutable original nor requires an explicit refresh reason.
    `cache_ttl=0` executes the duplicate, while progress enforcement permits the first
    stagnant round and resets after any novel call or fresh user segment.
12. `done` Export owner events for read identity observation/replay, compaction,
    and progress-guard decisions; require a joined
    `READ_OBSERVED -> CONTEXT_COMPACTED -> READ_REPLAYED` chain plus blocking
    impact before deterministic RCA promotes the producer-side cause. Demote the
    trajectory-only read replay from root cause to blocking mechanism.
13. `done` Cluster batch failures by verified structural causal signature
    (`owner + violated invariant + trigger + mechanism + guard failure`) and keep
    earliest-divergence buckets separate. Similar symptoms without the same verified
    root do not share a root-cause cluster.
14. `in_progress` Implement the read reuse/freshness execution contract itself and
    validate owner events plus attribution on fresh same-segment, post-compaction,
    continuation, explicit-refresh, mutation-invalidation, and held-out tau3 cases.
15. `done` Separate official business success from non-blocking execution and
    conversation quality. Emit deterministic quality findings and batch clusters for
    exact unchanged reads, public internal-reference leakage, raw action-schema
    presentation, and recovered response-review rejections; reserve naturalness and
    tone for a calibrated semantic judge.
16. `done` Provide one inspection path over local artifacts and Langfuse.
    The unified task report must contain business/quality findings, task-bound
    exceptions, a compact complete observation topology, generation-level and
    aggregate token usage, compaction token deltas, and causal events. Remote
    observability failure must not erase deterministic local RCA.
17. `done` Add quality-root attribution independent of business failure.
    For cross-continuation read replay, require the same owner-produced
    `read_identity` before and after a compacted continuation, a changed work-item
    scope, and the later guard decision `ALLOW_NEW_EVIDENCE`. Report the violated
    progress-state scope invariant without changing business root-cause status.
18. `in_progress` User-requested fresh task20 automation validation on 2026-09-11.
    Run one train task20 attempt (offset 15/count 1), seed 300, max steps 80,
    user max tokens 512, unchanged configured models and no completion override.
    Output to `artifacts/eval/tau3-task20-auto-analysis-rerun-2026-09-11`.
    Hypothesis: the unified pipeline independently reports the stochastic business
    outcome and detects quality findings/root causes from the new trace. Record
    ALL/ENV/ACTION, termination, writes, duplicate reads, public-output issues,
    verifier rejections, true errors versus control events, generation/token
    coverage, compaction events and causal-probe status. This exposed development
    rerun does not establish held-out generalization, and no production edits are
    permitted while it runs.

Validation:

- Successful task20 now retains `root_cause_status=OPEN` for the absent business
  failure while independently reporting `quality_root_cause_status=VERIFIED`.
  Seven identical read identities cross from the original worker graph through a
  compacted continuation into a fresh work item; every second observation was
  classified `ALLOW_NEW_EVIDENCE`. The verified quality root is
  `READ_NOVELTY_AUTHORITY_SCOPED_TOO_NARROWLY`, owned by `agent_progress`, and its
  structural causal signature is emitted in `quality_root_clusters`. An authorized
  refresh decision is a tested non-root counterexample. Relevant validation now
  passes with `118 passed, 3 skipped`.
- The unified `inspect` command fetched successful task20 into one report with 32
  traces, 348 compact execution-chain nodes, 23 owner causal events, and complete
  usage for 35/35 generations: 303,569 input, 6,157 output, 220,032 cache-read,
  and 529,758 provider-total tokens. It also records three compaction deltas.
  Expected `await_resume` approval/field interrupts are now two `status_events`,
  not errors; the real Langfuse error count is zero. Cost remains unavailable
  because no generation has matching cost details.
- Langfuse v2 observation reads explicitly request
  `core,basic,time,io,metadata,model,usage,metrics`; default core/basic reads do
  not contain token usage. Unified inspection and surrounding suites pass with
  `116 passed, 3 skipped`.
- Reanalysis of successful task20 produces `business_status=PASS` and
  `quality_status=WARN` simultaneously. Deterministic evidence contains seven exact
  unchanged read replays (14 message steps), five public internal references across
  two messages, four raw identifier field labels in the approval presentation, and
  two recovered response-review rejections. These form four quality clusters while
  `failure_clusters` remains empty; naturalness/tone remains a calibrated semantic
  judge concern rather than a deterministic root-cause claim.
- Quality-lane and surrounding RCA/progress/compaction/Langfuse/transition tests:
  `116 passed, 3 skipped`; diff whitespace validation passes.

- Fresh task20 trace: Langfuse session
  `tau3-7cbc237738de4b3f8f97a7b1bb415712`, worker trace
  `28fd5f442ad29ab3167ed24cacc826ec`. Four product reads at
  `19:39:36`, `context_summary` at `19:39:48`, then the same four product
  reads at `19:39:57` under the same `retail_agent` / `execute_work_item`.
- Run-revision envelope reconstruction: available context `30968` tokens,
  system/tool overhead `14654`, compaction soft/hard thresholds `21677` /
  `26322`, per-result inline threshold `4078`. The four original product
  results were each `223-792` approximate tokens, so ingestion did not discard
  them; the causal transition was later working-history compaction.
- New attribution tests: `96 passed, 3 skipped` across tau3 RCA, causal probes,
  progress, compaction, Langfuse export/extraction, review admission, and source
  continuity. A two-task synthetic batch merges the same verified causal signature;
  a replay symptom with a different/unverified trigger remains in a separate bucket.
- Backward check on the saved task20 artifact writes `rca-v3.json` with the read
  replay classified as a verified blocking mechanism and `root_cause_status=OPEN`,
  because that historical run contains no owner causal events. This is intentional:
  the new analyzer does not retrofit a deterministic root from timing or prose.

- 256 focused context-budget, planning, trace, Langfuse, and tau3 RCA tests pass;
  4 are skipped through the project's native virtual environment.
- Repository-wide pytest completes with 4288 passed, 653 skipped, and 35 failures,
  the same failure count seen before synchronization. The repository-wide suite is
  not green; fresh-run closure does not rely on treating those failures as passed.
- Python compilation and diff whitespace checks pass.
- Historical fixed10 v2 generates candidates only for task8 and task13. In v3,
  both pass; task4's ACTION deviation remains reference-only and task16 is an
  unavailable run rather than a business failure.
- Historical operation-plan analysis distinguishes task19 recovered context errors
  from task20's failed write and co-occurring runtime mechanisms.
- Historical artifacts predate `causal.*` metadata, so probes remain inconclusive.
  A fresh instrumented run is required to validate promotion on real model behavior.
- Read-only Langfuse validation fetched task13 (24 traces, 558 observations) and
  task8 (38 traces, 779 observations). Task8 produced 44 bounded semantic
  observations. Pagination and session binding work; both historical sessions
  contain zero standardized causal events, as expected.
- Owner events now bind the application-owned `control_id + control_revision`.
  Task impact is independently derived from objective findings; producers cannot
  self-declare a task-blocking root cause. Co-occurrence is insufficient: the
  event and blocking evidence must share an action/requirement or evaluator link.
- A real verifier-model run over enriched fixed10 v2 task8/task13 returned
  `UNKNOWN` for both semantic hypotheses with explicit missing evidence. Both
  remained `root_cause_status=OPEN`; no score or root cause was fabricated.
- Fresh task20 run `tau3-7cbc237738de4b3f8f97a7b1bb415712` did not reproduce
  `CONTEXT_BUDGET_EXCEEDED`. It terminated at 81 trajectory messages with
  `max_steps=80` while a compound approval signal remained pending.
- Deterministic replay analysis found 14 redundant calls across 7 exact read
  signatures. Each signature returned the same response hash, no write occurred,
  and the 28 consumed message steps exceed the 4 steps needed to remain within the
  configured budget and continue the pending interaction. The root cause is
  `REDUNDANT_READ_REPLAY_EXHAUSTED_STEP_BUDGET`; response verification failures are
  retained as co-occurring mechanisms rather than promoted as causes.
- The fresh artifact generated reviewable regression candidate
  `tau3-4d2b5f707f174217`. It remains a candidate until its business contract and a
  scope-preserving variant receive explicit review.

Exit criteria:

- Tasks 8, 13, 19, and 20 preserve their reviewed distinctions.
- Provider/evaluator failures cannot become business failures.
- Recovered errors cannot become blocking root causes.
- A hypothesis becomes verified only through a named passing probe with matching
  evidence and artifact version.
- Regression candidates require a reviewed contract before entering the gate set.
- The gate fails on newly blocking regressions and reports unavailable evidence
  separately.
- The feature branch contains the integrated RCA implementation; unrelated dirty
  worktree changes remain untouched.

Fresh rerun 2026-09-11:

- Task20 ran once with seed 300 and `max_steps=80` into
  `artifacts/eval/tau3-task20-auto-analysis-rerun-2026-09-11`; session
  `tau3-c55ea725553f4249a9b1416155294020`.
- The write committed and the selected publication records `task_completed=true`,
  but the final assistant response occupied step 80. Tau checked the limit before
  another simulator turn could emit `USER_STOP`, so official ENV/ACTION checks were
  skipped by `termination_gate_only`. The analyzer now reports
  `business_status=COMPLETED_UNSCORED`, not a business failure.
- Nineteen exact redundant reads consumed 38 message steps. They are not assigned to
  one root: causal events separately expose 11 cross-continuation identities admitted
  as `ALLOW_NEW_EVIDENCE`, 5 same-work-item replays allowed after compaction, and 2
  blocked replay attempts. Because causal events do not yet join the source business
  call, internal observation read, and subsequently emitted business call, these event
  counts are explicitly `UNJOINED` from the 19 external calls and do not claim to
  partition them. Each verified root reports only its event-level evidence scope.
- Unified inspection fetched 58 traces, 500 observations, 50 causal events and
  complete usage for 43/43 generations: 475,369 input, 10,930 output, 290,176
  cache-read, and 776,475 provider-total tokens. Eight compactions were observed;
  there were zero true Langfuse errors and two expected status events.
- Added general regression coverage for a committed completion at the step boundary
  and for preserving `EVALUATION_BLOCKING` through causal promotion. Focused
  evaluation/progress/compaction/Langfuse suites pass with `168 passed, 3 skipped`.
- This is a fresh development-run diagnosis, not held-out closure. The owner-level
  product repair is to persist completed read identities at the continuation-visible
  progress authority and require an explicit freshness transition before rereading.

Causal join implementation 2026-09-11:

- Future runs now retain the complete private chain from source business call, through
  persisted observation read, to the next emitted internal call and its official tau3
  call ID. The pre-execution reuse owner also records why it reused or dispatched.
- Exact repeats are promoted per call only when this chain and a deterministic reuse
  decision agree. `POLICY_DISABLED` and a missing reusable record are verified causes;
  explicit refresh and invalidation stay conditional rather than being mislabeled.
- Verified per-call causes are clustered across tasks by owner, cause code and source
  path. The existing task20 artifact correctly remains UNJOINED because it predates
  these fields. No paid benchmark rerun was performed for this instrumentation change.
