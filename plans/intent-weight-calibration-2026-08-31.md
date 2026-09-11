# Intent fusion calibration and weight selection

## Goal

Produce an evidence-backed V1 weight/threshold baseline and compare it with the typed V2 policy using compact public datasets, without selecting parameters on upstream test data or the consumed 100-case regression suite.

## Positive contract

- Only unambiguous upstream labels are mapped to DialogPilot intents.
- Upstream train/dev data owns parameter selection; upstream test remains sealed until the candidate is frozen.
- LLM/local/semantic raw signals are captured once per input fingerprint and replayed for every candidate.
- Source confidence scales are calibrated before weighted fusion.
- Selection optimizes grouped cross-validated Macro-F1 with explicit OOS and account-security recall gates.
- Final reporting separates external auto-mapped, synthetic regression, English, and Chinese evidence.
- No candidate is promoted solely because of aggregate accuracy.

## Steps

- [x] Audit existing builders, label mappings, cached source outputs, and dataset split provenance. (done)
- [x] Build compact development and sealed external suites from BANKING77 and CLINC150 OOS. (done; Bitext skipped because CDLA-Sharing acceptance was not authorized)
- [x] Implement input- and classifier-fingerprinted source capture for arbitrary evaluation bundles. (done)
- [x] Implement source calibration, grouped cross-validation, constrained weight/threshold search, and bootstrap uncertainty. (done)
- [x] Add invariant and leakage tests for split ownership and selection behavior. (done)
- [x] Capture/reuse raw signals and select a frozen candidate on development data only. (done)
- [x] Run the frozen candidate once on upstream test plus the consumed regression suite. (done)
- [x] Compare current V1, calibrated V1, and typed V2; document the promotion decision. (done)
- [x] Run the full repository test suite and finalize artifacts. (done)

## Tracked files

- `scripts/build_eval_dataset.py`
- `evaluation/intent_fusion_ablation.py`
- `scripts/run_intent_fusion_ablation.py`
- `evaluation/intent_weight_calibration.py` (planned)
- `scripts/run_intent_weight_calibration.py` (planned)
- `tests/test_intent_weight_calibration.py` (planned)
- `data/eval/intent-weight-calibration-2026-08-31/` (planned)
- `artifacts/eval/intent-weight-calibration-2026-08-31/` (planned)
- `docs/intent-weight-calibration-2026-08-31.zh-CN.md` (planned)

## Outcome

- The first calibrated candidate improved Dev by 1/500 but regressed upstream test by 2/500; rejected.
- Root cause was an undefined banking project scope plus cross-label few-shot contamination.
- The V3 owner-level contract improved Dev current V1 from 418/500 to 466/500.
- On a fresh unused 300-case upstream test, current V1, the calibrated candidate, and typed V2 all scored 283/300.
- Production weights remain 0.70 / 0.20 / 0.10 with threshold 0.50.
- Full repository suite: 318 passed.
