# Command-primary P0–P4 convergence plan

Status: completed

## Objective

Complete the bounded work through P4 without claiming production traffic or
SOTA: make command-primary the sole decision authority in the main runtime,
freeze a customer-service Command/Clarify/OOS gold contract, attribute failures
by the stage that owns them, evaluate a shadow-only Flow retriever against the
full Registry, and add structured clarification plus deterministic multi-turn
clarification evaluation.

## Constraints

- Preserve unrelated dirty-worktree changes.
- Legacy intent may remain as a compatibility projection, but it must not own
  route, work, risk, requirements, tools, approval, flow mutation, or
  publication.
- Retrieval remains observation-only through P4; it cannot hide Registry flows
  from the Structured LLM or authorize Encoder ACCEPT.
- Development data may drive iteration. Existing SGD Test remains unconsumed.
- Do not report tool/execution/publication failures from a runner that never
  executes those stages.

## Positive contracts

1. **Authority** — `CommandPrimaryChatPlan` and Registry-derived plan data are
   authoritative. Replacing the legacy intent projection cannot change any
   production decision or result.
2. **Gold** — every reviewed customer-service case has state, message/history,
   supported Flow set, expected command or typed terminal decision, arguments,
   and expected transition. Ambiguous cases have an explicit clarification
   reason and discriminating dimensions.
3. **Attribution** — each evaluated case receives exactly one earliest-owned
   outcome stage from a closed algebra; unavailable downstream stages are
   reported as not executed rather than guessed.
4. **Retrieval** — the shadow retriever returns ranked Registry Flow refs with
   reproducible scores. Reports include Recall@K, all-required-flow Recall@K,
   OOS behavior, token estimate, and latency; runtime behavior is unchanged.
5. **Clarification** — clarification decisions carry candidate flows, missing
   dimensions, and a typed reason. A multi-turn evaluator verifies necessary
   clarification, unnecessary clarification, candidate elimination, one-turn
   resolution, final command, and added turns.

## Steps

1. **completed** — Trace command-primary and legacy intent producers and all
   consumers across the main chat path; write authority invariance tests.
2. **completed** — Close any proven authority leaks at their owning boundary and
   migrate affected exceptional/media/publication paths.
3. **completed** — Define and validate a project-authored customer-service command gold
   contract with deterministic Dev/Heldout separation.
4. **completed** — Add closed-stage failure attribution and apply it to the
   existing SGD Dev artifact without inventing unexecuted failures.
5. **completed** — Implement deterministic shadow Flow retrieval and Full-vs-TopK
   evaluation with multi-command set recall.
6. **completed** — Implement structured clarification decisions and a deterministic
   multi-turn clarification suite/evaluator.
7. **completed** — Run focused and repository-level verification, update docs,
   and record remaining P5+ work without consuming SGD Test.

## Files produced or modified

- `plans/command-primary-p0-p4-convergence-2026-09-03.md` — this plan.
- `docs/command-primary-p0-p4-report.zh-CN.md` — bounded outcomes and evidence.
- `data/eval/customer-service-command-gold-v1/` — frozen synthetic contract.
- `artifacts/eval/sgd-command-dev-v2/diagnostics-p2-p3.json` — P2/P3 report.
- `artifacts/eval/locked-l0-clarification-v2/` — strict P4 live Dev run.

## Verification evidence

- Focused command-primary suite: 42 passed, 2 skipped.
- Full suite: 892 passed, 156 skipped, 3 unrelated failures (one pre-existing
  RAG bundle contract mismatch; two missing database environment variables).
- Strict live legacy-scope Clarify diagnostic: 5 CLARIFY, 8 OOS, 7 invalid
  provider outputs. Its Product/Installation oracle exceeds the bounded Registry,
  so 5/20 is not a valid quality score for the new Command Gold.
- SGD Test and customer-service Heldout were not consumed.
