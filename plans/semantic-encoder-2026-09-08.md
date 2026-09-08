# Contextual semantic encoder candidate

Status: candidate_rejected; experiment implementation/verification and independent
review complete; scoped delivery is the commit containing this record. Requested reliable multi-turn fast path remains
unresolved, not closed. Baseline HEAD 95f42f1; concurrent business/RAG edits excluded.

## Owner and positive contract

EncoderInput owns the same role-ordered input in training and inference. A pretrained
sequence classifier jointly encodes recent dialogue and current message. Existing
FastPathPolicy owns acceptance and trusted argument binding; query authorship,
approval, ambiguous continuation and writes remain with their existing owners.
One selected classifier per language, no sequential ensemble or extra LLM router.

Observed gap: character features are additive across turns; independent challenge
had only one multi-turn positive per language (zh low confidence; en disabled class).
This does not prove all contextual classification fails. It does expose insufficient
multi-turn acceptance evidence and motivates a bounded semantic model comparison.

## Frozen experiment before execution

- One pretrained compact semantic backbone per language, Transformers standard
  sequence-classification training; at most two development fits per language.
- Existing v3 training/calibration/development splits plus grouped contextual
  counterfactual families. Same current answer with different history; denial,
  correction, compound goals, irrelevant-history invariance. Synthetic attribution.
- New independently authored challenge frozen before model predictions; never train
  on it. Whole conversation families remain within one split.
- Existing .88 calibration Wilson lower bound, .98 per-class development precision,
  >=10 accepts unchanged. Adoption additionally requires >=.98 full-policy precision,
  zero unsafe accepts, >0 and improved contextual positive accepts on independent
  challenge, and no drop in overall independent accepted-correct coverage per language.
- CPU warmed inference P95 <=100ms on bounded inputs, measured separately from load;
  larger local cost must be offset by model-call savings. No business writes/e2e.
- Training budget: local GPU <=2 GPU-hours, max 4 epochs/fit, batch sized to available
  memory; do not interrupt other processes. No reranker training. No API data generation
  unless locally authored data proves inadequate; report changes before expanding.

## Steps

1. done inspect model resources/API, freeze data and independent challenge.
2. done train and calibrate semantic candidate; preserve rejected outcomes.
3. done evaluate actual FastPathPolicy, CPU latency and contextual invariants.
4. done final review; both language candidates rejected, no promotion;
   independent review, report and scoped commit/push. Otherwise retain current default
   and report candidate failure without pretending implementation is adopted.

Non-goals: restructure task graph, change approval/query contracts, online example
retrieval, duplicate inference runtimes, claim SOTA or population precision from synthetic data.

## Development observation (before independent predictions)

Initial zh fit: knowledge 37/37 passes, product 65/71 and refund 96/99 fail.
The existing calibration selector maximizes coverage subject only to .88 Wilson
lower bound, which does not require its empirical precision to meet the later .98
adoption target. For this semantic candidate, strengthen calibration to require
both .88 Wilson and .98 empirical precision, using calibration only. Old selector
behavior remains unchanged for old artifacts. This is a stricter criterion, not a
lowered threshold; retain initial manifests and compare before/after explicitly.
The independent challenge has not been predicted on or used to choose thresholds.
English cache initially contained only tokenizer_config; downloaded missing files
at the already pinned revision. No fit occurred on the failed load.

First full-policy outcome: contextual accepts zh0->6/20, en0->18/20; however zh
accepted a live inventory request as knowledge in regression, and en dropped the
purchase part of a compound request in independent data. Both adoption gates fail.
These expose broad capability-boundary/compositional coverage, not missing keyword
rules. Second (last) fit adds compositional second-goal operators to whole source
utterances, read-only restriction positives, and business-vs-knowledge negatives,
preserving source family splits. No original challenge text is added to training.
First challenge becomes regression; independent author froze new160 cases
(sha256 d4ac3dabe9580950346f622cfca0fc94ad17aec7d683f8ca180a7622665be736)
before second-candidate predictions. If this fit fails, do not tune on that set.
An intermediate data build rejected duplicate case IDs before training; index-based
variant IDs fixed at the data builder, not runtime. Final data semantic-encoder-v2-final.
Before final challenge: enforce the predeclared zero-unsafe-accept requirement at
the per-capability development gate too. A class that accepts a DEFER example is
disabled even if aggregate precision exceeds98%; unrelated passing classes remain
eligible. This uses development labels only, not independent challenge feedback.
Pre-final checks found one exact generic English payment question in the first
challenge also present in pre-existing development data. First challenge therefore
is not entirely fresh; keep its result with this caveat, never use it as final proof.
The final160 challenge has no exact input overlap with train/calibration/development;
the evaluation entrypoint now checks this before predictions. No scored example was
removed or relabelled to improve a score.

## Final outcome and containment

Final fresh160: zh11/11 accepts correct, contextual3/24 (baseline2/24); en16/17,
contextual9/24 (baseline0/24). Zh regression17/18 also fails98% gate. Both
artifacts/eval/semantic-encoder-final-2026-09-08/adoption.json passed=false.
No post-final training, threshold adjustment, or example-specific runtime rule.
Single-label+DEFER training improved some context recognition but did not reliably
preserve the whole request across policy/state vs knowledge and compound goals.

Baseline also wrongly accepts composite requests in the fresh challenge. Existing
TARGET_ENCODER_ENABLED default and example config nowfalse as TEMPORARY containment;
explicittrue is not overridden, services not restarted. This is not a root-cause
closure or evidence of fast-path success. Removal requires fresh whole-request
accuracy, compound-goal coverage and old-regression gates together, at useful cost.
No candidate runtime is selected in production; existing ConversationAgent handles
deferred turns. No new inference dependency in production.

62 code regressions pass including real PostgreSQL;2 tau2-dependent skipped.
Without PG config60pass/4skip. Unit tests validate contracts, not model task success.
Isolated staged-index snapshot also60pass/4skip; verification does not depend on
unrelated uncommitted application or RAG changes.
Docs: docs/semantic-encoder-2026-09-08.zh-CN.md. Preserve first and second predictions,
training configs, hashes, failed class gates, source attribution, and independent data.
Independent reviewer verified final counts, data disjointness and the lack of a
second online model path; no further training or production branch warranted.
Commit/push identity is tracked by Git, not a fabricated closure attestation.
