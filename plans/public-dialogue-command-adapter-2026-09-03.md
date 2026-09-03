# Public dialogue command adapter plan

Status: done

## Goal

Add a reproducible, versioned adapter that converts an official public
task-oriented dialogue dataset into DialogPilot conversation-level command
evaluation cases without treating projections as authoritative production
labels.

## Target contract

- The source dataset and its schema remain the authority for source turns,
  intents, required slots, service calls, and dialogue state.
- A dataset-specific registry owns the supported Flow algebra used by adapted
  cases; it does not mutate the production registry.
- Every emitted label records its source annotation and deterministic mapping
  rule.
- Ambiguous turns are excluded with typed reason codes rather than guessed.
- Development and held-out splits preserve the official source split.
- Evaluation uses deterministic code grading for command kind, Flow, arguments,
  and state/transition fields.

## Steps

1. **done** — Audit existing command/evaluation contracts and select the
   public source plus the exact unambiguous mapping surface.
2. **done** — Implement typed adapted-case contracts, source loader,
   deterministic converter, exclusions, and dataset-specific registry output.
3. **done** — Add a CLI that downloads or consumes the official source,
   freezes adapted artifacts, hashes them, and writes a conversion report.
4. **done** — Implement deterministic scoring and split-aware summaries.
5. **done** — Add unit/property tests covering supported mappings,
   ambiguity fail-closed behavior, provenance, and scoring.
6. **done** — Run the adapter on official data, run relevant tests, and
   document reproducible usage plus honest resume/reporting language.

## Files produced or modified

- `plans/public-dialogue-command-adapter-2026-09-03.md` — this plan.
- `evaluation/public_sgd/` — contracts, converter, stratified selector,
  deterministic scorer, and frozen-artifact validator.
- `scripts/adapt_sgd_command_dataset.py` — official source to full pool CLI.
- `scripts/select_sgd_command_benchmark.py` — stable-hash balanced freeze CLI.
- `scripts/score_sgd_command_predictions.py` — exact-match scorer CLI.
- `scripts/validate_sgd_command_benchmark.py` — checksum/reference/invariant gate.
- `tests/test_public_sgd_adapter.py` — mapping, fail-closed, scoring, selection,
  and integrity tests.
- `data/eval/sgd-command-balanced-v1/` — 1,200 Dev + 1,200 Test frozen cases.
- `docs/sgd-command-benchmark.zh-CN.md` — contract, results boundary, and usage.

## Verification evidence

- Official source commit: `e852981ae34990f4358979625854259302feaa78`.
- Full pool: Dev `15,415` cases; Test `31,620` cases.
- Frozen balanced set: Dev/Test each contain exactly 300 cases for each of
  CLARIFY, first call, repeat call, and out-of-registry.
- Dataset validator: `valid=true`, `case_count=2400`.
- Targeted tests: `6 passed`.
- Oracle scorer smoke: all dimensions `1.0`; this validates the evaluator only
  and is not a model result.
