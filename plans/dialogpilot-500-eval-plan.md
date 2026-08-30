# DialogPilot 500-case layered evaluation plan

Goal: build a deterministic, versioned 500-case project evaluation set that
measures four different causal layers instead of treating external intent data
as an end-to-end benchmark.

## Contracts

- Exactly 500 cases: intent 180, routing 120, retrieval 100, stateful 100.
- Exactly 400 dev and 100 heldout cases; semantic variants sharing a group_id
  never cross splits.
- Intent uses licensed external pressure data; other layers are grounded in
  DialogPilot task owners, corpus evidence IDs, memory contracts, tool policy,
  trace and fail-closed behavior.
- Generated/project-authored cases remain provisional until human review; no
  metric may be described as project gold before review status changes.
- Every layer has deterministic truth and a scorer contract. LLM Judge is not
  allowed to own route sets, evidence IDs, memory isolation or tool effects.

## Steps

1. [completed] Audit current dataset schema, scorer and runtime support.
2. [completed] Implement deterministic 500-case builder and source definitions.
3. [completed] Generate dataset, corpus and manifest; validate counts/splits/groups.
4. [completed] Extend tests and registry validation for the new dataset.
5. [completed] Document layer design, review workflow and honest metric language.
6. [completed] Run all gates, push, and verify CI/Pages.

## Produced files

- `plans/dialogpilot-500-eval-plan.md` — this status/decision record.
- `scripts/build_project_eval_500.py` — deterministic layered dataset builder.
- `data/eval/dialogpilot-500-v1/` — 500 cases, 25-document corpus and manifest.
- `docs/evaluation-500.zh-CN.md` — GitHub Pages design/run/interview guide.
- `tests/test_layered_eval_dataset.py` — distribution and route-contract gates.

## Audit decisions

- `evaluation.dataset.DatasetBundle` owns structural truth: the manifest must
  declare and enforce layer/split distributions, not leave counts to a report.
- Routing labels are authored expectations and are checked against the current
  deterministic `TaskPlan` builder; the builder must not silently derive gold
  from whatever the implementation happens to return.
- Stateful cases carry structured setup/action/assertion protocols so they can
  become executable regression scenarios instead of prose-only questions.
- The existing 500 external intent candidates remain source pools. The project
  benchmark selects 180 balanced pressure cases and does not relabel the other
  320 cases as multi-agent, RAG or memory tests.

## Verification

- Dataset load/checksum/distribution: passed (500 cases, 25 corpus documents).
- Current Planner vs all routing expectations: 120/120 matched.
- Repository tests: 116 passed.
- GitHub Actions CI and Pages deployment for commit `1346c07`: passed.
- Runtime truth after Phase 2: API executes intent/routing; the dedicated
  runner executes stateful fixtures; retrieval still needs its isolated
  collection producer.

## Phase 2: executable stateful scenarios

Goal: replace symbolic stateful setup names with a fail-closed fixture registry
that invokes repository-owned memory, tool-policy, trace and publication code,
records observed facts, and produces scorer-compatible predictions.

1. [completed] Inventory the real stateful owners and map the 50 scenario
   actions to supported executable contracts.
2. [completed] Implement fixture registry, isolated probes and typed unsupported
   failures; never derive observations from expected assertions.
3. [completed] Run 80 dev and 20 heldout stateful cases with auditable reports.
4. [completed] Add invariant/negative tests for fixture completeness, zero-effect
   evidence and missing observations.
5. [completed] Update dataset/page commands and limitations.
6. [completed] Run repository gates, push each coherent phase, verify CI/Pages.

### Phase 2 evidence

- 100/100 cases resolve to one of 22 registered real-owner fixtures.
- Dev: 80/80; heldout: first run 19/20, after owner repair 20/20.
- The first heldout run exposed an escaped-markup budget defect in
  `ContextAssembler`: pre-render sizing could discard a high-priority memory
  section after HTML expansion. The owner now fits against final rendered text.
- Negative gates prove an invented action and an assertion without a probe both
  fail closed.
- Repository gate: 123 tests passed; GitHub Actions CI and Pages deployment for
  `092eeb0` passed, and the public evaluation page exposes the updated runner.
