# Context and interaction convergence

Status: IMPLEMENTED / deterministic and PostgreSQL validation passed. Fresh paid
model/tau quality validation remains open. Baseline 2dec913; preserve unrelated dirty RAG/archive work.

Scope: remove blanket domain outcome judging for ordinary interaction/termination;
single-question publication without unconditional composition; separate pinned
current task from revisable background; bounded current tool evidence with archived
original access. Keep runtime authority for states, approval, operation identity and
receipt. No paid tau rerun while provider balance unavailable.

Owners: InteractionBoundary owns handback detection; Runtime owns actual tool
outcomes; ResponseAssembler owns presentation selection; task input adapter owns
task/background separation; ContextCompaction owns the SDK working view; archive
owns full originals. Policy/TaskGraph/publication remain authoritative.

Positive contract: a single ordinary question can reach Publication with no review
or composition call; waiting identity and resume data survive. Action proposals retain
their bounded semantic check and deterministic policy. Background may be summarized
without changing current objective/constraints. Oversized tool material is visibly
incomplete and readable by reference; no successful receipt or fact is synthesized
from a preview. Non-reading consumers cannot treat pointers as proof.

Plan:
1. Trace producers/consumers and current dirty changes (done).
2. Implement owner changes and migrate interface/persistence consumers together (done).
3. Verify state/call-count/preservation properties and PostgreSQL recovery; compare
   saved input sizes with fixed estimators. Independent fresh-context review.
4. Record limitations and commit/push only relevant work. Do not claim model quality
   or ten-task business closure from deterministic tests.

## Delivered contract

- Domain handback detection no longer invokes a judge for COMPLETE, BLOCKED or
  NEEDS_USER_INPUT. These markers do not establish business execution success;
  governed tools, requirements and receipts still drive result conversion.
- Bound question-only presentation uses PASS_THROUGH with NOT_REQUIRED semantic
  verification and an integrity hash. `interaction_ready` is distinct from
  `verified`; approval presentation still requires verification. Single/multiple
  questions can be published without another author or judge only when no other
  retained/current outcome, approval, receipt or unresolved sibling needs delivery.
  Mixed responses retain assembly and final claim checks. No regex classifier,
  model-supplied "safe question" flag or additional LLM gate was added.
- PREPARE_ACTION retains deterministic plan checks and its existing semantic
  review. Ordinary actor context no longer carries a hypothetical review budget.
  Review overflow without a model call is explicitly distinguished in call counts.
- Model-facing current task contains objective/arguments/requirements, current
  user wording, approval/decision/receipt state. Background contains source history
  and supplied facts. Application-owned old task-view IDs are replaced on resume;
  actual tool history, source archives and public transcript are preserved.
- Large read results and supplied facts are archived and exposed by reference using
  the existing page reader. Initial inline threshold uses 25% of available working
  input after fixed overhead; this is a tunable allocation, not a claimed optimum.
  Oversized evidence directories reduce to a locator rather than an unbounded preview.
- Tool-less action review hydrates offloaded originals, including nested supplied
  facts, unless visible successful nonempty reader pages are present. Oversized
  hydrated inputs request bounded reading before another proposal; they are not
  sent over capacity or approved from a preview. Original side effects are not retried.
- Initial current-task admission no longer silently trims recent dialogue. Existing
  historical-observation read references remain supported; editable history is
  archived/summarized by the one SDK working-context editor.

## Verification and measurements

Local suite: 267 passed / 10 environment-dependent skipped across interaction,
domain outcomes, review projection/admission, context compaction, historical context,
framework Agent, response assembly and approval tests.
Real PostgreSQL runs: 77 passed (Agent checkpoint/process reopen, context, review
recovery); 116 passed (HTTP, domain handback, historical context, interaction).
Independent fresh-context review: 13 focused tests passed, no remaining blocking
owner/publication/persistence defect in this bounded change. Review findings were
incorporated (retained results, semantic attestation separation, nested references,
cleared/empty reader results). No paid models or business benchmark writes ran.

Reproduce historical partition measurement:
`python scripts/measure_task_context.py artifacts/eval/context-projection-before-after-2026-09-09/tau3-1de67ef6aba748ddbe325b7705c4ee48.json`

| Captured outcome | Old fixed task | New fixed task | Editable background | New total |
| --- | ---: | ---: | ---: | ---: |
| COMPLETE | 7754 | 374 | 7395 | 7769 |
| PREPARE_ACTION | 7593 | 210 | 7398 | 7608 |

SDK approximate tokens, same captured inputs. Partition alone does not reduce total
tokens: it makes background editable without changing protected task semantics.
The captured COMPLETE no longer triggers domain review. Separate regression proves
single-question path uses one author call, zero domain/response review and zero
composer calls, while preserving target binding. Large-result tests retain exact
original artifacts with small working views at multiple data/budget sizes.

## Limits / next validation

Question-only tool instructions do not mathematically guarantee factual purity of
arbitrary model wording. NOT_REQUIRED must not be reported as evidence-supported.
Choosing relevant/sufficient evidence pages also remains a model-quality task.
Runtime approval/version/idempotency boundaries are unchanged and do not infer
authorization from question wording. Fresh held-out semantic validation and the
previous ten-task business results remain unproven pending provider availability.
Do not restore blanket judges or add per-business phrase rules to hide that limit.
