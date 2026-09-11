# Fresh intent V1/V2 sealed validation

## Goal

Run the existing V1 fusion and fixed V2 fusion policy against the 100 independently reviewed synthetic cases without tuning on their outcomes.

## Constraints

- Verify the submitted SHA-256, schema, review status, unique IDs, slice counts, and contextual history before inference.
- Pass only `input.message` and `input.history` to the recognizer; review notes and labels are scoring-only data.
- Capture expensive/non-deterministic LLM outputs once and replay V1/V2 from the same raw signals.
- Keep this suite classified as `independently_reviewed_synthetic`, not human gold.
- Do not change weights, thresholds, prompts, or policy after seeing this suite's outcomes.

## Steps

- [x] Verify candidate and review artifacts.
- [x] Add a reviewed-candidate loader and fixed-policy sealed-report path.
- [x] Add tests for schema, leakage boundaries, and no-tuning report semantics.
- [x] Capture LLM, pattern, n-gram, and BGE semantic signals once.
- [x] Score V1 and V2 overall and by slice; inspect fixes, harms, conflicts, and failures.
- [x] Run the full test suite and publish the evidence report.

## Outcome

- Current V1: 96/100.
- Frozen V2: 94/100.
- V2 versus V1: 1 fix, 3 harms.
- Full regression suite: 310 passed.
- No policy parameters were selected on the sealed suite.

## Tracked artifacts

- `fresh-intent-candidate.jsonl`
- `fresh-intent-review.md`
- `evaluation/intent_fusion_ablation.py`
- `scripts/run_intent_fusion_ablation.py`
- `tests/test_intent_fusion_ablation.py`
- `artifacts/eval/fresh-intent-v1-v2-2026-08-31/`
- `docs/fresh-intent-v1-v2-validation-2026-08-31.zh-CN.md`
