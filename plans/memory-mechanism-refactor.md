# Memory mechanism refactor plan

Goal: replace rolling summary/profile overwrite semantics with sequenced raw
events, range-owned summary checkpoints, high-water commits, and versioned
facts while keeping Redis/Chroma as the current storage adapters.

Constraints:

- Do not add PostgreSQL, a queue, LangGraph, LangMem, or another runtime.
- Preserve `ContextAssembler` as the prompt-budget owner and
  `HybridMemoryRetriever` as the ranking owner.
- Preserve unrelated user work in the dirty worktree.
- Synchronize the GitHub Pages architecture/tutorial content with the actual
  supported behavior.

## Steps

1. **completed** — Establish current storage/test contracts and define the
   positive event/checkpoint/fact algebra.
   - Files inspected: `memory/conversation_memory.py`, `memory/context.py`,
     `api/main.py`, `tests/test_context_memory.py`,
     `tests/test_hybrid_memory.py`, `docs/architecture.md`, Pages sources.
2. **completed** — Implement sequenced events and range-owned summary
   checkpoints with checkpoint-only CAS.
3. **completed** — Replace whole-profile merge with source-linked fact
   operations and active-fact projection.
4. **completed** — Migrate callers and context projection without changing the
   final prompt-budget owner.
5. **completed** — Add invariant-focused unit/state-machine tests and run the
   relevant regression suite.
6. **completed** — Synchronize README, architecture, and GitHub Pages sections;
   perform negative-source searches for obsolete contracts.

## Target contract

- Every persisted message has a conversation-local monotonic `seq` and stable
  `message_id`.
- A summary chunk covers exactly `[from_seq, to_seq]` and never consumes an old
  summary as source material.
- A checkpoint advances only from its expected version/high-water state; new
  messages above the selected high-water mark do not invalidate the commit.
- Long-term scalar facts have one active value, retain source message IDs, and
  preserve superseded values instead of overwriting a profile document.
- Recent raw messages remain real user/assistant messages; summaries and facts
  remain tagged, untrusted context projections.
- Redis/Chroma adapter failure semantics remain typed or safely retryable; no
  claim of production durability beyond the current adapters is introduced.

## Produced files

- `plans/memory-mechanism-refactor.md` (this plan)
- `memory/conversation_memory.py` — event/chunk/checkpoint/fact owners.
- `api/main.py` — batched turn persistence and fact-source handoff.
- `tests/test_context_memory.py`, `tests/test_hybrid_memory.py`,
  `tests/test_chat_handoff.py` — positive invariants and caller migration.
- `evaluation/stateful_runner.py`, `evaluation/fresh_stateful_fixtures.py`,
  `scripts/build_project_eval_500.py`, `data/eval/*` — executable contracts
  migrated away from Redis-clear and mutable-profile assumptions.
- `README.md`, `docs/architecture.md`, `docs/project-pitch.md`,
  `docs/interview-guide.md`, `docs/full-architecture-tutorial.zh-CN.md` —
  repository and GitHub Pages documentation synchronized.

## Verification

- `python -m pytest -q`: 160 passed.
- `python -m compileall -q api memory evaluation tests`: passed.
- `git diff --check`: passed.
- Negative source search found no remaining current-contract references to
  rolling-summary replacement, Redis clearing, or whole-profile merge.
