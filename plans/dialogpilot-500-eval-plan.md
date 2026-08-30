# DialogPilot 500-case layered evaluation plan

Goal: build a deterministic, versioned 500-case project evaluation set that
measures four different causal layers instead of treating external intent data
as an end-to-end benchmark.

## Contracts

- Exactly 500 cases: intent 180, routing 120, retrieval 100, stateful 100.
- Exactly 400 dev and 100 heldout cases; semantic variants sharing a group_id
  never cross splits.
- Intent uses licensed external pressure data; other layers are grounded in
  DialogPilot task owners, corpus evidence IDs, memory contracts, tool policy,
  trace and fail-closed behavior.
- Generated/project-authored cases remain provisional until human review; no
  metric may be described as project gold before review status changes.
- Every layer has deterministic truth and a scorer contract. LLM Judge is not
  allowed to own route sets, evidence IDs, memory isolation or tool effects.

## Steps

1. [completed] Audit current dataset schema, scorer and runtime support.
2. [completed] Implement deterministic 500-case builder and source definitions.
3. [completed] Generate dataset, corpus and manifest; validate counts/splits/groups.
4. [completed] Extend tests and registry validation for the new dataset.
5. [completed] Document layer design, review workflow and honest metric language.
6. [completed] Run all gates, push, and verify CI/Pages.

## Produced files

- `plans/dialogpilot-500-eval-plan.md` — this status/decision record.
- `scripts/build_project_eval_500.py` — deterministic layered dataset builder.
- `data/eval/dialogpilot-500-v1/` — 500 cases, 25-document corpus and manifest.
- `docs/evaluation-500.zh-CN.md` — GitHub Pages design/run/interview guide.
- `tests/test_layered_eval_dataset.py` — distribution and route-contract gates.

## Audit decisions

- `evaluation.dataset.DatasetBundle` owns structural truth: the manifest must
  declare and enforce layer/split distributions, not leave counts to a report.
- Routing labels are authored expectations and are checked against the current
  deterministic `TaskPlan` builder; the builder must not silently derive gold
  from whatever the implementation happens to return.
- Stateful cases carry structured setup/action/assertion protocols so they can
  become executable regression scenarios instead of prose-only questions.
- The existing 500 external intent candidates remain source pools. The project
  benchmark selects 180 balanced pressure cases and does not relabel the other
  320 cases as multi-agent, RAG or memory tests.

## Verification

- Dataset load/checksum/distribution: passed (500 cases, 25 corpus documents).
- Current Planner vs all routing expectations: 120/120 matched.
- Repository tests: 116 passed.
- GitHub Actions CI and Pages deployment for commit `1346c07`: passed.
- Runtime truth after Phase 2: API executes intent/routing; the dedicated
  runner executes stateful fixtures; retrieval still needs its isolated
  collection producer.

## Phase 2: executable stateful scenarios

Goal: replace symbolic stateful setup names with a fail-closed fixture registry
that invokes repository-owned memory, tool-policy, trace and publication code,
records observed facts, and produces scorer-compatible predictions.

1. [completed] Inventory the real stateful owners and map the 50 scenario
   actions to supported executable contracts.
2. [completed] Implement fixture registry, isolated probes and typed unsupported
   failures; never derive observations from expected assertions.
3. [completed] Run 80 dev and 20 heldout stateful cases with auditable reports.
4. [completed] Add invariant/negative tests for fixture completeness, zero-effect
   evidence and missing observations.
5. [completed] Update dataset/page commands and limitations.
6. [completed] Run repository gates, push each coherent phase, verify CI/Pages.

### Phase 2 evidence

- 100/100 cases resolve to one of 22 registered real-owner fixtures.
- Dev: 80/80; heldout: first run 19/20, after owner repair 20/20.
- The first heldout run exposed an escaped-markup budget defect in
  `ContextAssembler`: pre-render sizing could discard a high-priority memory
  section after HTML expansion. The owner now fits against final rendered text.
- Negative gates prove an invented action and an assertion without a probe both
  fail closed.
- Repository gate: 123 tests passed; GitHub Actions CI and Pages deployment for
  `092eeb0` passed, and the public evaluation page exposes the updated runner.

## Phase 3: convergence review after repeated reopening

Status: in progress. Phase 2 implementation is complete, but verified closure
was withdrawn after an independent review found a shared acceptance-gap pattern.
The 20 previously named heldout Stateful cases are now regression cases because
they were inspected and used during repair.

### Causal model

- `ContextAssembler` owns the final prompt budget, but `_fit_sections` accounted
  individual rendered blocks rather than the exact joined representation. The
  second allocation pass also returned both history slack and already-unused
  section capacity, so the same tokens could be allocated twice.
- The Stateful runner owns execution evidence, but its gate checked only that an
  assertion key existed. Seven cases could therefore report a fact without
  traversing the production method that owns that fact.
- The documentation projected scenario names into stronger claims (idle timeout,
  signed approval tokens, public-response projection, cross-user retrieval) than
  the executable fixtures actually established.
- The old heldout split and fixture-key checks were examples, not a proof of the
  supported budget algebra or semantic execution path.

### Positive target contracts

1. For every accepted prompt input, mandatory tokens plus the exact rendered,
   joined sections and retained history are at most `max_input_tokens`. If the
   mandatory current turn cannot fit, assembly raises a typed budget error.
2. Section separators, escaping and description attributes are charged at the
   final representation boundary. History slack may be reassigned once, using
   `available - used_history_tokens`, without duplicated capacity.
3. Empty-query and empty-corpus cases execute `MemoryManager.search_long_term`;
   fallback cases force `_summarize` through `_fallback_summary`; explicit close
   cases claim only `finalize_conversation`; injection cases contain hostile data.
4. Mutation tests must fail when any reviewed semantic Owner is bypassed. Seeded
   generative tests must establish the prompt-budget invariant over varied
   sections, descriptions, markup, history and limits.
5. Pages, manifests and reports describe the existing heldout as consumed
   regression evidence. A fresh heldout and Reviewer B remain separate gates and
   cannot be self-attested by an author who has seen the cases and repairs.

### Steps

1. [completed] Repair prompt-budget ownership and add typed overflow behavior.
2. [completed] Replace the seven false-positive fixture paths and add mutation tests.
3. [completed] Regenerate the deterministic dataset and re-run all regression gates.
4. [completed] Correct Pages/report scope, wire the isolated RAG producer and
   publish the convergence evidence.
5. [pending] Obtain fresh-context Reviewer B plus newly authored unseen cases
   before restoring a verified-closed or human-gold status.

### SOTA comparison and bounded non-goals

Anthropic's 2026 agent-evaluation guidance separates tasks, trials, graders,
transcripts, outcomes and harnesses, and recommends grading the authoritative
outcome and relevant trace rather than trusting fluent output. Research on
holdout contamination likewise treats repeatedly inspected evaluation examples
as regression data, not unseen generalization evidence. This phase adopts those
boundaries without adding a generalized event-sourcing system, a new agent
framework, or fictitious signed-token/public-API guarantees that production does
not implement.

## Phase 4: chunk-aware RAG convergence

Status: implemented; independent re-verification remains open. Reviewer B established that the storage adapter persisted a
unique `document_id::chunk-N`, but `KnowledgeBase.search` replaced that candidate
identity with the parent `document_id` before BM25/RRF. Projection then looked up
metadata independently by parent ID, so content, chunk index and ranks could come
from different chunks.

### Positive contract

- Ingestion uses a hard token-estimate ceiling, structural sentence/paragraph
  boundaries where possible, and a bounded overlap; even one oversized sentence
  is split into valid chunks.
- `chunk_id` remains the candidate identity through vector recall, lexical recall,
  RRF and selected-hit projection. `document_id` remains stable parent identity.
- Projected `content`, `chunk_id`, `chunk_index`, `title`, score and ranks all
  originate from the same selected chunk.
- Result diversity collapses duplicate parent documents only after ranking, while
  preserving the highest-ranked aligned chunk evidence.
- Existing short-document retrieval metrics remain reproducible; multi-chunk and
  boundary-overlap cases receive deterministic regression tests.

### Steps

1. [completed] Implement token-aware structural overlap chunking.
2. [completed] Preserve chunk candidate identity through search and projection.
3. [completed] Add long-sentence, overlap-boundary and multi-chunk alignment tests.
4. [completed] Re-run repository tests and provisional Retrieval Dev baseline.
5. [completed] Update Pages/review status, push implementation and documentation,
   then verify CI and Pages.

### Verification evidence

- Production repair: `6235141`; documentation and immutable Reviewer B evidence:
  `8a8d3de`.
- Local and GitHub CI: 146 tests passed; GitHub Actions run `33317140968`
  completed successfully.
- Pages run `33317140576` completed successfully, and the deployed architecture
  page exposes the 360-token ceiling, 48-token overlap and chunk-identity contract.
- Retrieval Dev remains provisional at 80 cases: Recall@5 0.9125, MRR 0.7504 and
  nDCG@5 0.7914. An embedded-Chroma multi-chunk probe returned one aligned
  `chunk_id`, chunk index, content and rank record.
- This phase records implementation completion only. Reviewer B's repository-wide
  `reject` decision and the fresh independent closure gate remain unchanged.

## Phase 5: Reviewer B stateful and tool-lifecycle convergence

Status: in progress. This phase addresses the remaining shared acceptance and
lifecycle gaps from Reviewer B without changing its historical review record.

### Positive contract

- Fixture result producers receive an immutable request containing case identity
  and scenario inputs but no expected answer. Expected truth exists only in the
  scorer after actual evidence has been produced.
- Every controlled tool call has one correlated terminal audit outcome. Timeout
  and cancellation are explicit states; a write whose business commit cannot be
  observed is reported as `outcome_unknown`, never as zero side effect.
- Host approval is control-plane state. Model parameters named `approved` or
  `approval_token` cannot authorize a call and never reach a tool handler.
- Queries containing only Unicode whitespace/format controls return before either
  long-term-memory storage path is invoked.
- Every action in the sealed Reviewer B fresh dataset is either executed through
  a registered production-owner fixture or reported as a typed coverage gap; no
  unregistered action can be counted as a pass.

### Steps

1. [completed] Separate FixtureRequest from EvalCase and add an expected-copy attack gate.
2. [completed] Close timeout/cancellation/approval-parameter lifecycle semantics.
3. [completed] Normalize Unicode format-only memory queries before storage access.
4. [completed] Register and execute all 27 fresh Reviewer B actions.
5. [completed] Run invariant, mutation, full-suite, Dev/regression and fresh gates.
6. [in progress] Update Pages and push each coherent repair phase.
