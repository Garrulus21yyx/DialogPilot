# Intent Fusion Mini Ablation — 2026-08-31

## Goal

Evaluate the current intent fusion contract on a bounded dataset without claiming production accuracy:

- current LLM 0.70 + n-gram 0.20 + pattern 0.10;
- LLM/pattern conflict and specific-intent refinement;
- n-gram versus an available semantic embedding backend;
- OTHER rejection and confidence threshold behavior.

Target size: 50–100 cases per evaluation slice, with a small frozen regression split.

## Constraints

- Reuse versioned public/project data where licensing and provenance are already recorded.
- Keep public label mappings explicit; do not silently change expected labels to match predictions.
- Cache per-source `(intent, confidence, failure)` outputs so weight sweeps do not repeat LLM calls.
- Treat auto-mapped/provisional labels and any consumed split as regression evidence, not production gold.
- Preserve unrelated working-tree changes.

## Steps

| Step | Status | Evidence / files |
|---|---|---|
| Inspect evaluation infrastructure, available datasets, runtime credentials, and existing predictions | done | Running service on `:18000`; 180 intent + 120 routing cases; cached BGE models; live model configured in service environment |
| Define four bounded slices and explicit selection/mapping contract | done | Reuse BANKING77/CLINC150 and project routing scenarios; 50 business, 50 rejection, 50 conflict, 52 grouped similarity cases |
| Implement source-output capture and offline fusion replay | done | `evaluation/intent_fusion_ablation.py`, two runner scripts |
| Run deterministic tests and available live-model/embedding evaluations | done | 202 LLM captures (0 failures), n-gram, Pattern, BGE-M3, 200 offline candidates; 17 tests passed |
| Analyze metrics, conflicts, confidence, and limitations | done | machine-readable report plus counterexample audit |
| Document reproducible commands and final recommendation | done | `docs/intent-fusion-mini-ablation-2026-08-31.zh-CN.md` |

## Decisions

- No weight is accepted as "better" from aggregate accuracy alone; Macro-F1, OOS behavior, security recall, and conflict slices are required.
- If a semantic embedding backend is unavailable, report that as an unexecuted comparison rather than substituting a different fact under the same label.
- The compact suite reuses already-consumed/provisional labels and will be called a diagnostic regression suite, never a fresh heldout.
- Conflict cases use the routing fixture's declared primary intent; they do not prove multi-label intent coverage.

## Produced files

- `plans/intent-fusion-mini-ablation-2026-08-31.md` — this plan and progress log.
- `evaluation/intent_fusion_ablation.py` — selection, fusion replay, metrics, and coarse search.
- `scripts/run_intent_fusion_ablation.py` — recoverable source capture and report runner.
- `scripts/capture_semantic_intent_source.py` — dependency-isolated semantic capture.
- `tests/test_intent_fusion_ablation.py` — fusion and dataset invariants.
- `docs/intent-fusion-mini-ablation-2026-08-31.zh-CN.md` — human-readable report.
- `artifacts/eval/intent-fusion-mini-2026-08-31/*` — cases, templates, raw sources, report.
