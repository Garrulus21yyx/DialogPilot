# Tau3 automated evaluation and RCA loop

The loop consumes saved tau3 runs, establishes objective failures, tests causal
lineage, creates reviewable regression contracts, compares candidate runs with a
baseline, gates regressions, and optionally publishes compact scores to Langfuse.
Offline analysis and gating do not invoke a model or require Langfuse credentials.

## End-to-end flow

```text
tau3 run artifacts
  -> analyze objective evidence
  -> fetch optional Langfuse evidence
  -> run causal probes
  -> generate regression candidates
  -> review the expected contract
  -> activate regression suite
  -> run candidate agent version
  -> compare and gate
  -> publish compact Langfuse scores
```

Analyze a run and optionally enrich it from Langfuse:

```bash
python scripts/tau3_eval_loop.py analyze artifacts/eval/<run> \
  --output artifacts/eval/<run>/rca.json
python scripts/tau3_eval_loop.py enrich-langfuse artifacts/eval/<run>/rca.json \
  --output artifacts/eval/<run>/rca-enriched.json
```

The enrichment command fetches all traces in each task session, paginates their
observations, extracts standardized causal metadata, and reruns the probes.

Generate regression candidates:

```bash
python scripts/tau3_eval_loop.py candidates artifacts/eval/<run>/rca-enriched.json \
  --output regression-candidates.json
```

Generated cases remain `CANDIDATE`. A review file activates accepted contracts:

```json
{
  "reviews": [{
    "candidate_id": "tau3-0123456789abcdef",
    "decision": "APPROVE",
    "reviewer": "support-quality-owner",
    "reviewed_at": "2026-09-09T10:00:00+00:00",
    "reason": "Expected write and final state are required by policy",
    "contract": {
      "forbidden_findings": ["MISSING_REQUIRED_WRITE"],
      "minimum_scores": {"env_reward": 1.0}
    }
  }]
}
```

```bash
python scripts/tau3_eval_loop.py promote regression-candidates.json reviews.json \
  --output regressions.json
```

After running the candidate Agent version, apply the CI-compatible gate:

```bash
python scripts/tau3_eval_loop.py gate baseline-rca.json candidate-rca.json \
  --regressions regressions.json --output gate.json
```

The gate exits `0` for pass, `1` for a measured regression, and `2` when evidence
is incomplete. Default cross-run comparison uses ENV because ACTION can reject an
equivalent valid trajectory. A policy file can opt into stricter metrics:

```json
{"comparable_scores": ["env_reward", "action_reward"], "fail_on_missing_tasks": true}
```

Pass it with `--policy policy.json`. Reviewed contracts independently decide
whether a specific ACTION score or finding is mandatory.

Publish session-level Langfuse scores after analysis:

```bash
python scripts/tau3_eval_loop.py publish candidate-rca.json \
  --output langfuse-publication.json
```

The publisher writes idempotent `tau3_run_available`, `tau3_business_pass`,
`tau3_rca_status`, and `tau3_failure_codes` scores. It omits business pass when ENV
is unavailable, so provider failures cannot become false business zeroes. Full
evidence stays in local artifacts.

## Causal event contract

Owners emit events into Langfuse observation metadata using the `causal.` prefix:

```json
{
  "causal.event_id": "event-123",
  "causal.sequence": 12,
  "causal.event_type": "OUTCOME_REVIEWED",
  "causal.task_id": "8",
  "causal.turn_id": "turn-5",
  "causal.owner": "domain_reviewer",
  "causal.goal_revision_id": "revision-2",
  "causal.work_item_id": "work-1",
  "causal.proposal_id": "proposal-3",
  "causal.approval_id": "approval-3",
  "causal.tool_call_id": "call-4",
  "causal.receipt_id": "receipt-4",
  "causal.reason_code": "DOMAIN_OUTCOME_REJECTED",
  "causal.outcome_impact": "TASK_BLOCKING"
}
```

Required fields are `sequence`, `event_type`, `task_id`, `turn_id`, and `owner`.
Sequences are strictly increasing per task. Probe event types are `GOAL_REVISED`,
`WORK_ITEM_RESUMED`, `ACTOR_DECISION`, `OUTCOME_REVIEWED`, `PROPOSAL_CREATED`,
`ACTION_APPROVED`, `EXECUTION_FAILED`, and `TOOL_COMMITTED`.

A stale revision or execution error becomes a verified root cause only when the
same lineage carries `outcome_impact=TASK_BLOCKING`. Missing lineage yields
`INCONCLUSIVE`; recovered errors cannot become blocking root causes.

## Evidence semantics

- `OBSERVED` records a score, state, or action-reference deviation.
- `VERIFIED` at the `mechanism` layer records an event that definitely occurred.
- `SUPPORTED` records a causal candidate and the evidence still needed.
- `VERIFIED` at the `root_cause` layer requires an owner-bound causal probe.

Unkeyed batch logs remain `RUN_ONLY`. Evaluator outages remain `EVALUATION_ONLY`.
Provider failures remain `RUN_BLOCKING` and do not become business zeroes.

The calibrated witnesses are task8 (scope revision), task13 (target set and
simulator consistency), task19 (recovered context failure plus judge outage), and
task20 (missing write with context and citation failures).

Langfuse integration follows [Scores via API/SDK](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk),
[Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk),
and [Experiments in CI/CD](https://langfuse.com/docs/evaluation/experiments/experiments-ci-cd).
