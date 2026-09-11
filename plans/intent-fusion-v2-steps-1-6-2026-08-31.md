# Intent Fusion V2 — Steps 1–6 — 2026-08-31

## Goal

Implement and evaluate the first six optimization steps derived from the 202-case mini ablation:

1. separate and clean semantic prototypes;
2. add an explicit BGE semantic-similarity Provider boundary;
3. require top-1 threshold and top-1/top-2 margin for semantic acceptance;
4. make Pattern emit spans and positive/negative/quoted polarity;
5. add a typed `FusionPolicyV2` that preserves an accepted specific LLM decision and only permits legal parent-to-child refinement;
6. dual-run V1 and V2 on the existing 202-case diagnostic regression suite.

## Positive target contract

- V1 remains behaviorally unchanged and remains the runtime default.
- Semantic prototypes have one authoritative label per normalized text; generic prototypes do not reuse supported specific expressions.
- Semantic evidence contains top-1, top-2, score, margin, threshold, accepted, and provider failure.
- Pattern evidence contains intent, matched span, offsets, keyword, and polarity; V2 consumes only positive evidence.
- V2 returns a typed status (`classified`, `out_of_scope`, `ambiguous`, `provider_failure`) and a deterministic reason.
- A specific accepted LLM result is never demoted to `OTHER` merely because auxiliary signals disagree.
- Generic-to-specific refinement is legal only when the specific intent projects to the LLM generic parent and independent semantic + positive Pattern evidence agree.
- Semantic failure has a typed deterministic fallback; unsupported configurations fail explicitly.

## Steps

| Step | Status | Evidence / files |
|---|---|---|
| Inspect current intent owner, consumers, configuration, and dirty overlaps | done | V1 constants already centralized; `confidence` controls OTHER clarification; semantic ML deps are absent from app venv |
| Implement prototype and evidence contracts | done | `_SEMANTIC_PROTOTYPES`, `SemanticEvidence`, `PatternEvidence` |
| Implement explicit BGE Provider and typed V2 policy | done | `core/intent_fusion_v2.py`, optional semantic requirements |
| Add invariant/unit tests without changing V1 behavior | done | `tests/test_intent_fusion_v2.py`; existing V1 tests retained |
| Extend capture/report tooling for margin, Pattern polarity, and V2 tuning | done | raw top-2/margin and 266 Pattern evidence records; 25 V2 candidates |
| Run V1/V2 on 202 cases and audit held regression evidence | done | V1 167/202; V2 171/202; V2 fixes 4, harms 0 versus current V1 |
| Update documentation and record limitations | done | updated mini-ablation report with V2 section |

## Constraints

- Existing modifications in `core/intent_recognizer.py` and other dirty files belong to the user; preserve unrelated work.
- Do not switch the production default from V1 during this task.
- The 202 cases are consumed auto-mapped/provisional diagnostics, not fresh Gold.
- No weight/threshold is promoted from aggregate Accuracy alone.

## Produced files

- `plans/intent-fusion-v2-steps-1-6-2026-08-31.md` — this plan.
- `core/intent_recognizer.py` — separate immutable V2 semantic prototypes; V1 alias preserved.
- `core/intent_fusion_v2.py` — evidence types, BGE Provider, polarity extraction, V2 policy.
- `requirements-semantic.txt` — explicit optional semantic runtime.
- `tests/test_intent_fusion_v2.py` — provider, rejection, polarity, authority, and transition invariants.
- `evaluation/intent_fusion_ablation.py` — V2 replay and threshold/margin diagnostics.
- `scripts/run_intent_fusion_ablation.py` and `scripts/capture_semantic_intent_source.py` — top-2/margin and Pattern evidence capture.
- `docs/intent-fusion-mini-ablation-2026-08-31.zh-CN.md` — V1/V2 evidence and limitations.
