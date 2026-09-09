# Approval continuity repair

Status: implementation and local contract verification complete; no semantic
closure claimed. Base HEAD: 3eee99f.

## Evidence and scope

Previous 60f1a1b reduced action identity duplication but left confirmation
semantics in prompts. The current causal surface is action tool presentation,
pending decision interpretation, whole-turn compilation, resume, and publication.
Unrelated RAG, archive pagination, and documentation edits remain untouched.

Observed mechanisms:
- Preparation tool descriptions embed execution descriptions that require prior
  confirmation, although runtime collects confirmation after preparation.
- A bound typed decision with any text is returned to global planning, where a
  text-only response can omit it.
- `review_action` conflates questions with conditional assent.
- More directly, `pending_approval.typed_decision` was put in `runtime_context`,
  while the provider explicitly instructed that runtime_context is not user assent.
  The current-input projection therefore contradicted the input's provenance.

## Positive contract

One concrete proposed action owns its arguments, approval identity and continuation.
Missing choices complete arguments; they do not grant execution. A decision and
independent requests coexist. Changes or conditions must be resolved before a
write; an omitted interpretation must never silently authorize a write. Execution
consumes the prepared identity and feeds its receipt back to remaining work.
Existing TaskGraph, PendingApproval and framework checkpoints remain authoritative.

## Work

1. done: trace owners and choose the smallest coherent contract change.
2. done: converge tool presentation and pending decision/extra-input handling.
3. done: generated decision matrix, resume/publication regression checks,
   independent fresh-context review.
4. in_progress: record implementation vs verified scope, commit and push scoped files.

No paid model or tau rerun in this change. Local scripted models establish contract
properties, not actual model accuracy. Current reference practices: LangChain HITL
pauses a concrete proposed call and resumes that call with a bound decision;
Anthropic tool design recommends unambiguous tool contracts. Reviewed 2026-09-09:
https://docs.langchain.com/oss/python/langchain/human-in-the-loop
https://www.anthropic.com/engineering/writing-tools-for-agents

## Implemented ownership and data flow

- ConversationAgent projects a versioned current_user_decision separately from
  pending_approval. The latter contains application facts only. The native
  HumanMessage carries current text, decision and supplied input values together.
  Capture replay is lossless; historical capture decoding remains audit-only.
- The same planning call can approve/decline and propose independent work, or
  hold the decision and answer/investigate/revise. Hold is not a new persisted
  approval state: existing PendingApproval remains until a real transition.
- A typed decision omitted by the model has explicit APPROVAL_REPLY_UNADDRESSED
  failure, not a successful response, implied approval, or generic transport error.
  This detects a protocol failure; it does not claim to improve generation by itself.
- Preparation tool descriptions expose only the preparation calling protocol.
  Original business prerequisites/effects remain in a named execution-reference
  section, not appended as instructions for a different tool. No text stripping,
  business-specific rewriting, new runtime, or confirmation regex was added.
- Manager owns the distinction between new user input and observing already
  executed reads. Observation keeps the original audit input but does not resubmit
  its approval or field signals. Existing TaskGraph, operation_key, receipt,
  checkpoint identity and Publication deduplication remain unchanged.

## Verification notes

Independent review found the observation-phase resubmission gap before delivery;
fixed at Manager.prepare_observation. A fresh-context reviewer added two complete
manager tests: hold + read + commit + observe + respond + commit. They assert one
read, unchanged pending identity, preserved facts, no fabricated grant or receipt.

First local group: 449 passed, 14 PostgreSQL-dependent skips. Expanded initial
matrix uncovered only fixture assumptions that hold consumed approval; corrected
to assert retention and absence of source-checkpoint import. First real PostgreSQL
group: 316 passed, one failure because the scripted HTTP provider read the removed
typed_decision field. Migrated that consumer to current_user_decision.

Final checks:
- Local 14-module group: 501 passed, 14 database-dependent skips; 3 existing SDK
  warnings in structured-transport tests. No network model calls.
- Real PostgreSQL 8-module group: 319 passed; 1 existing multiprocessing fork
  warning. Includes HTTP, approval execution, process-exit checkpoint recovery,
  compound decisions, and observation continuity. Counts overlap, not additive.
- `git diff --check`: clean.
- Independent fresh-context review's observation blocker is fixed and its new
  two-case complete-manager regression passes.

Persistence impact: no database or business checkpoint format migration; hold is
  interpretation-only and maps to existing pending state. Planning input/provider
  and framework producer versions advance; no alternate old live approval path.

Limits: scripted providers verify contract/lifecycle, not natural-language accuracy.
Moving execution documentation into scoped reference reduces mixed instructions
but actual duplicate-confirmation frequency still needs fixed-model evaluation.
No claim that all prior tau tasks now pass, and no paid rerun performed.
