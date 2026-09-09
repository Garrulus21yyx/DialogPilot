# Read reuse across planning, delegation and continuation

Status: causal inspection and implementation complete for source-navigation loss
and conflicting objective authority; task20 behavioral validation remains open.

Baseline: 6491142, preserving unrelated dirty files. User requests causal repair,
not additional steps or a duplicate-call ban. Context-management and planning
skills guide separation of working history, source records and model inputs.

Evidence: `/tmp/tau3-rca-rerun-task20-ownertrace-20260909-2140`, session
`tau3-7cbc237738de4b3f8f97a7b1bb415712`. Identical read responses and absence of
writes prove redundant read cost, not which producer/consumer lost context.

1. Done: join saved trajectory with actual model inputs and source code;
   distinguish hidden facts, missing dependencies, new task versus continuation,
   and deliberate refresh. Trace all relevant producers and consumers.
2. Done: specify positive reuse contract at proven owner; migrate affected
   boundaries without exposing unrelated child working histories.
3. Done: deterministic transition tests including read/delegate/input/resume,
   scope isolation, freshness and mutation; no paid task rerun until repair checked.
4. Done: record scope, remaining uncertainty and verification; delivery identity
   is the commit containing this file, pushed separately after checks.

Do not declare global closure from example counts. Archive existence alone does
not prove useful context delivery, and response equality does not prove a future
read is safe to cache. No generic TTL/cache or write-policy bypass is authorized
by this diagnosis.

## Established causal boundary

Official CLI observations read: 51 generations, session matched; downloaded to
`/tmp/task20-reuse-generations.json`. No new model/business calls for diagnosis.

- `bb3280be9769d7e5` initial Worker had one full verified order fact and two
  historical observations. This falsifies "parent never passed the order".
  Its output explicitly explains the repeat: verified state is processed but the
  objective says delivered. This is planning prose contradicting facts, not a
  missing-result failure. Role instructions now make that authority boundary
  explicit for both the planner (do not relabel state) and worker (use verified
  state, not an assumed prerequisite in the objective). This change is semantic
  guidance, not a deterministic guarantee of future model choices.
- `52f72b74e1133b7f` pending-order Worker had two verified facts and six historical
  observations. It obtained the four product results.
- `f7fd453973963a99` explicitly paged existing result references. Then
  `9eefe78036e476f6` summarized old history. The model-facing individual-source
  directory was absent: graph tool_observations remained for artifact restoration,
  but the model only saw summary plus whole-history archive reference.
- `0b237e9cf6893a66` explicitly requested fresh order/product data to recover full
  product details; `3572301e6e16ec18` re-read all four products.
- On input continuation another summary `44ff6f825ee531d3` preceded
  `630f00947ee2eccd`, which again requested order/account verification. The native
  ANSWERED tool interaction was present. Continuation itself did not vanish.

Shared proven compaction defect: lossless storage existed, but navigation continuity was
delegated to lossy summary generation. Not a proven missing DAG dependency in
this run, nor proof that every repeat has one cause. Initial read is explained by
the contradictory objective; policy-based discretionary refresh remains model
behavior, not data loss.

## Repair and boundaries

ContextCompaction retains an application-generated source directory when applying
SDK summaries, covering native archived results, supplied verified facts and
Publication references. No changes to business execution, DAG, cache, model
budget, authorization or freshness decisions. Existing scoped Store readers are
reused. Index is retained through continuation and rebuilt deterministically on
subsequent compactions even if the summary contains zero references. Status/time
and coverage remain observations; a successful past read is not a current grant.

Parameterized tests cover 1/4/9 reads, two compactions, continuation, original
readback, immutable input, scope denial and historical navigation. Relevant
PostgreSQL suite before final metadata checks: 188 passed, existing fork warning.
Implementation is not a claim that task20 now finishes or all 14 repeats disappear.
Old already-lossy checkpoints cannot recover omitted individual links from a
summary alone; their whole-history archives remain readable.

Final relevant working-tree suite: **213 passed**, one existing multiprocessing
fork deprecation warning. This includes stateful two-compaction/continuation
properties, archive scope, native messages, approval/input and real PostgreSQL
subprocess recovery. No paid task rerun, no change to evaluation scores.
Source-navigation recovery uses existing framework Store and readers rather than
a second cache or memory authority. Negative evidence remains explicit: structural
tests do not establish that a real model will never elect a discretionary refresh.
Clean staged-only export final run: **213 passed**, same fork warning. A new
expired-source fixture initially had expiry before observation and was correctly
rejected; both timestamps now form a valid historical record. No production
validation was relaxed. Unrelated dirty archive/worker/docs changes are excluded.
