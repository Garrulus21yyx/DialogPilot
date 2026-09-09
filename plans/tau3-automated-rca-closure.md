# Tau3 automated RCA closure

Status: implementation complete; fresh instrumented semantic validation pending

Goal: turn saved tau3 evaluations into an automated, evidence-backed loop that
analyzes failures, runs deterministic probes, creates reviewable regression
candidates, compares a candidate run with a baseline, gates regressions, and can
publish compact scores to the existing Langfuse trace lineage.

Constraints:

- Work only on `codex/tau3-automated-rca` in `/tmp/dialogpilot-tau3-rca`.
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

Validation:

- 15 focused tests pass through isolated pytest execution; repository-wide pytest
  collection is unavailable because global conftest imports absent `psycopg_pool`.
- Python compilation and diff whitespace checks pass.
- Historical fixed10 generates candidates only for task8 and task13; task4/16
  ACTION deviations remain reference-only.
- Historical operation-plan analysis distinguishes task19 recovered context errors
  from task20's failed write and co-occurring runtime mechanisms.
- Historical artifacts predate `causal.*` metadata, so probes remain inconclusive.
  A fresh instrumented run is required to validate promotion on real model behavior.
- Read-only Langfuse validation fetched task13 (24 traces, 558 observations) and
  task8 (38 traces, 779 observations). Pagination and session binding work; both
  historical sessions contain zero standardized causal events, as expected.

Exit criteria:

- Tasks 8, 13, 19, and 20 preserve their reviewed distinctions.
- Provider/evaluator failures cannot become business failures.
- Recovered errors cannot become blocking root causes.
- A hypothesis becomes verified only through a named passing probe with matching
  evidence and artifact version.
- Regression candidates require a reviewed contract before entering the gate set.
- The gate fails on newly blocking regressions and reports unavailable evidence
  separately.
- The main worktree remains unchanged.
