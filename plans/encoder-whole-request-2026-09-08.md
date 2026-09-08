# Whole-request fast-path experiment

Status: candidate_rejected; semantic candidates in 3ac5fd0 remain rejected. Default fast
acceptance remains temporarily disabled; unrelated business/RAG edits excluded.

## Causal review and target contract

Symptom: a high-confidence accepted class loses another requested action. Trigger:
compound goals or policy/state contrasts. Immediate mechanism: multiclass argmax
is compiled to one command; adapter hardcodes multi_intent=False. Shared gap:
category confidence is treated as sufficient whole-request coverage, although the
model has no candidate capability contract in its input. DEFER supervision exists,
so a different loss/input is a falsifiable hypothesis, not a proven cure.

Ownership: EncoderInput supplies role-ordered context; artifact scores semantic
eligibility; FastPathPolicy applies calibration, state, READ effect and provenance;
RoutePolicy validates the resulting command. No downstream verifier can recover a
goal omitted before planning. State-bound continuation remains ahead of Encoder.

Positive bounded contract: only one complete supported READ capability can bypass
planning. A relevant but insufficient capability is negative. All-negative or
ambiguous predictions defer; permissions/arguments do not become model authority.
The existing three capabilities are the scope, not all e-commerce tasks.

## Experiment frozen before execution

- Reuse semantic-encoder-v2-final data/splits without more templates or previous
  challenge examples. Expand each record into three (context, capability contract)
  pairs; positive iff its gold is that complete capability. DEFER is all-negative.
- One standard Transformers binary sequence classifier per language, batches of
  candidate pairs, no second LLM/classifier or custom loss/training loop.
- Same pinned backbones, seed17, four epochs, one fit/language; <=2 GPU-hours.
  Pair token budget384, reject training overflow; defer evaluation overflow.
- Same calibration .88 Wilson + .98 point precision, development >=10 accepts and
  zero DEFER false accepts. Same adoption precision>=.98 each scope, zero unsafe
  accepts, improved contextual correct coverage vs shipped baseline, no lower total
  correct accepts, warmed CPU entry P95<=100ms. No threshold changes after scoring.
- New independent challenge by fresh-context reviewer; prior three challenges are
  regression only. Freeze hash before predictions, verify exact split disjointness.
- Report single-turn/contextual/compound decisions, full policy acceptance, CPU cost
  and counting-planner savings separately; no claim of live LLM/E2E success.

## Work

1. done review owner/contracts and standard pair-classifier API.
2. done reuse Trainer for candidate-conditioned coverage, test algebra/order.
3. done one fit/language, CPU calibration, frozen evaluation and unchanged GPU replay; keep failures.
4. done independent review and documentation; scoped delivery recorded by Git. Production
   migration only if adoption passes; no closure based solely on unit tests.

References checked 2026-09-08: Transformers text classification (standard Trainer),
https://huggingface.co/docs/transformers/tasks/sequence_classification ; paired
cross-encoder scoring https://sbert.net/docs/cross_encoder/usage/usage.html . This
is a mature classification mechanism, not a demonstrated domain SOTA result.

Before training/scoring: reviewer froze independent160 SHA256
1500c1737f18bf4e58f6ce8b19a354271821eb7127495694851bd1936406eeef.
Binary COMPLETE argmax for more than one capability is a conflict: project all
candidate scores to zero and boundary to one, identically before calibration and
inference. This is the declared one-capability algebra, not a tuned confidence cap.
The 0.5 comparison is binary COMPLETE vs NOT_COMPLETE argmax (ties conservative),
not an alternative tuned admission threshold. Ordinary class thresholds still apply.

Before independent predictions: zh CPU calibrated product103/103, refund126/126;
knowledge63/64 with unsafe example disabled. English GPU development all classes
eligible (524/524,52/52,237/237); CPU recalibration pending. Neither is adoption.
Training runtime zh100.05s/en271.97s, no extra fit; pretrained classifier-head
initialization warnings are expected before supervised training, not swallowed errors.
Independent code review found no blocker; added boundary/tie tests as recommended.
72 local code checks passed,4 PostgreSQL/tau2-dependent skipped; no business calls.
Before independent predictions: include prior v2 semantic category artifacts as
an additional unchanged comparison on the same fresh/regression inputs, not only
the character baseline. Pairing expands training instances threefold and increases
input length, so do not claim a compute-controlled objective-only ablation.

User correction after frozen CPU result: deployment has RTX3080 GPU, so CPU-only
latency is not a sufficient deployment criterion. Add same-weight CUDA inference
replay (no retraining or threshold edits), preserving the original CPU outcome.
This diagnoses hardware cost; existing CPU-calibrated thresholds are not thereby
certified for GPU rollout. Accuracy failures remain failures on either device.

## Outcome

Fresh160 coverage candidate zh9/10/en22/25; context5/24 and9/24 (same-input prior
category2/24 and8/24). Regression330 zh40/41/en59/61. Both quality gates fail.
CPU P95zh102.17ms/en144.62ms; unchanged RTX3080 replay3.56ms/6.57ms, same selections
and errors. CPU latency assumption was not appropriate as sole deployment judgement;
GPU fixes throughput concern, not semantic omissions. No online model adoption.
Pairing-only hypothesis rejected as sufficient repair. Compound-goal omissions,
negation and policy/state contrasts remain; do not turn witnesses into runtime rules.
Tests76pass/4skip in final isolated index snapshot, including explicit CPU/CUDA
calibration-device propagation and manifest recording. Model evaluation used shared worktree
application state in both arms, not a clean full-system release certification.
Independent reviewer confirmed final scores, exact CPU/CUDA decision equivalence,
device transfer and lack of a second online path. User fast-path goal remains unresolved.
