# Planning context scope and complete request budget

Scope expanded by the user's request: main/child context isolation, historical
body offloading, public conversation window and complete planning request budget.
No paid benchmark, model traffic or production business write. Local PostgreSQL
integration fixtures create and remove their own isolated test database.

Observed mechanism: historical projection fits a partial payload before provider
adds instructions and tool schemas. Final validation rejects without resizing.
The specific historical failure lacks a full captured request; its exact token
breakdown remains unknown. Do not claim a reconstructed estimate proves it.

## Causal review and positive contract

Archiving, projection, compaction and admission had different scopes. Native
child messages were archived, but their facts re-entered main planning through
observed_execution and historical business_observations. Worker dispatch also
broadcast the parent fact collection, including unrelated sibling investigations.
A payload-only fit then ran before native system/contract/tools were assembled.
This explains the reachable amplification and admission gap; it does NOT prove
the unrecoverable historical request's exact token breakdown.

Authoritative owners remain unchanged:

- Transcript/ThreadSummary: public dialogue and its actual covered sequence.
- Child checkpoint/framework Store: local working messages and immutable originals.
- WorkPlan/ResultBoard: dependencies, retained outcomes and full business facts.
- Publication: historical observation originals and scoped read access.
- Provider: admission of the complete rendered model request.

Model projections now behave as follows:

1. Main planning gets public recent messages (existing configurable default 8),
   summary, current input, active tasks and approval state. Child reports contribute
   status, coverage and a short candidate report, not their tool investigation bodies.
   Conversation-level DIRECT reads remain available as actual observations.
2. Archived historical bodies become existing read_conversation_observation
   pointers when the pointer is smaller. Small values stay inline. This selection
   occurs before a budget crisis, even for the most recent completed investigation.
   It neither grants new authority nor changes original facts or provenance.
3. Workers get declared dependency facts, their own continuation facts/messages,
   and retained conversation-level DIRECT observations. Independent DELEGATED
   siblings are not a shared working-memory pool. Historical bodies use pointers.
4. Summary coverage is propagated from the existing MemoryManager checkpoint,
   through PostgreSQL read projection to Target context; 0 remains unknown, not an
   inferred watermark. Full-request budget trimming can remove summary-covered
   old messages, but not the last exchange or unsummarized restrictions.
5. Planning selection does not enforce a guessed whole-payload budget. Final
   provider measurement includes system, contract, native schemas, protocol and
   output reserve, using the same rendered envelope as invocation. Oversized
   irreducible input fails before a model call. Logs include component estimates
   and post-projection required capacity, not source contents.
6. Existing LangChain SummarizationMiddleware/ClearToolUsesEdit and framework
   Store paging continue to handle child rolling history, paired tool messages,
   and oversized fresh results. No second archive or hand-written summary loop.

Full ResultBoard facts, approval terms, receipts and source records are NOT
truncated. Final reply/verification still use their evidence contract; an oversized
mandatory evidence set is not made valid by handing a tool-less reviewer an
unreadable pointer. This change does not promise arbitrary inputs always fit.

## Reference practices (consulted 2026-09-09)

- [Claude Code context and subagents](https://code.claude.com/docs/en/how-claude-code-works):
  old tool-output clearing, summarization, isolated child windows and compact
  reports; also explicitly documents compaction thrashing for giant single inputs.
- [LangChain Deep Agents context engineering](https://docs.langchain.com/oss/python/deepagents/context-engineering):
  separates runtime/input context, offloading, summarization and child isolation.
- [LangChain short-term memory](https://docs.langchain.com/oss/python/langchain/short-term-memory):
  native message trimming/summarization and protocol-valid tool/result pairs.

These support responsibility boundaries, not a universally optimal token threshold.
We retain the installed framework and existing child thresholds, not migrate to
Deep Agents or copy Claude Code's provider-specific limits.

## Verification and scope

Parameterized tests vary archived body size, dynamic tool-schema size, sibling
count, public-window length, and summary coverage. Assert originals unchanged,
references resolvable, current approval/restrictions retained, child-body growth
does not enlarge parent planning, unrelated facts excluded, and oversized mandatory
input does not invoke a model. Existing tests cover latest tool-batch pairing,
archive paging/scope, summary preservation, checkpoint restore and artifact recovery.

Constructed historical-body measurements (project estimator, NOT provider usage):

| Body characters | Before estimated tokens | After reference projection |
|---:|---:|---:|
| 2,000 | 749 | 293 |
| 12,000 | 3,249 | 293 |
| 40,000 | 10,249 | 293 |

Pre-delivery suite: 492 passed, 27 skipped. PostgreSQL-enabled suite initially
had 194 passed / 4 failures: the observation publication test called an obsolete
fixture signature and used approval identity for a FIELDS signal. Updated that
test to the existing authoritative fixture, without changing production approval.
Final PostgreSQL-enabled rerun: **521 passed**, one Python multiprocessing `fork`
deprecation warning, no skips. This includes a real subprocess exit/recovery
test for the framework child graph and scoped historical observation replay.
Clean staged-tree check at `/tmp/dialogpilot-context-check.Elr1bF`: **521 passed**,
same one warning. This exported only tracked HEAD plus staged changes; unrelated
working-tree edits and their extra archive behavior were absent. Scoped changes
are ready for commit/push; delivery identity is the commit containing this file.

No historical τ³ closure claim. The original failed complete request was not
retained, so fresh task-level behavior remains separate from these structural
and persistence proofs. Unrelated dirty documentation/evaluation/archive-reader
changes are excluded from this delivery.
