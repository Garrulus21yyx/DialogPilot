# Native conversation output convergence

Status: implementation and bounded protocol verification complete; conversational
tool-selection quality remains open. Scope: replace forced public-response tool with the existing
SDK message protocol, not another parser, online judge or runtime.

Evidence: task22 on305e001 returns useful normal questions in Langfuse but the
application rejects them as planning_requires_action. Mixed text/tool messages
also contain drafting preamble. Prior acceptance supplied respond calls in fixtures
and therefore did not test the provider's ordinary text terminal.

Owner contract:
- SDK owns reasoning/text/tool parsing; use AIMessage.text and tool_calls.
- Planning with actions validates the complete batch; action outcomes own delivery.
  Text alongside approve/decline/work is not a pre-execution result. Hold may carry
  a natural reply without changing authorization.
- Planning with no actions accepts a nonempty completed natural reply. This is a
  first-class terminal, not a fallback. Text never creates approval or work.
- Composition is one ordinary model call with no tools. Reasoning blocks stay in
  traces; text is the reply candidate. Empty/refused/truncated/invalid-tool output
  remains typed failure, not a reason to block valid questions.
- Remove respond tool entirely. Existing approval identity, ResultBoard,
  Publication and evidence checks remain owners; no new per-business rules.
- Prompt explicitly requests direct customer wording, not narrated working notes.
  Plain-text semantic quality is not guaranteed by block filtering.

Catalog convergence: remove model-facing unsupported_request as well. It selected
an out-of-scope terminal for conversational closure in live probes. Explaining an
unavailable capability is ordinary conversation, not a privileged action. Internal
OOS enums/compiler support remain for existing non-model decisions; registry and
approval enforcement are unchanged. With no actions, invoke the unbound model
without tools or tool_choice. No alternate text parser or legacy fallback.
Observable changes: model-specific OOS route/reason no longer emitted; native
limitation replies use existing response verification and preserve pending waits
without automatically re-presenting them. Independent consumer audit confirmed
the removed model label was not an authorization boundary.

Primary references checked 2026-09-09:
- https://docs.langchain.com/oss/python/langchain/models (native tool loop and final_response.text)
- https://docs.langchain.com/oss/python/langchain/messages (typed reasoning/text/tool blocks)
- https://api-docs.deepseek.com/guides/anthropic_api/ (supports text, thinking, tool_use; documents any support)
These do NOT prove why the preceding live forced-any request returned plain text.

## Verification and honest limits

- 667 passed, 3 warnings, 54.40s across the same19 owner/consumer test modules as
  the preceding repair, with real local PostgreSQL. Includes action/text product
  and permutations, native SDK wire with empty/nonempty catalogs, typed incomplete
  responses, approval continuation, HTTP Publication and checkpoint replay.
- After the final shared delegation-description clarification:372 related tests
  passed in11.64s; overlaps with the above total, not additive.
- v20 explicitly rejects unpublished v19 checkpoints via the existing version
  gate; published replies retain their replay path. No automatic write reexecution.
- Independent fresh-context reviewer: 426 passed,3 PG deselected,8 additional
  adversarial checks (overlap, not added to main count). Removed the residual
  review_action instruction to call respond; migrated all native-response fixtures.
- No new dependency, parser, retry loop or online judge. Four production owners
  changed: action catalog/conversion, planning/compose SDK adapter, shared goal
  description, existing turn-version gate. Unrelated user edits untouched.

Live author-only probes used configured Flash planning / Pro composition,800
max tokens, no business tools executed and no model substitution.16 calls total,
four development rounds, not a heldout benchmark or best-of score:

1. Native text implementation: address request delegated; CN closure selected OOS;
   EN compose asked for email, CN compose asked blue/green directly (1.189/1.508s).
2. Clarified OOS description: CN closure and EN thanks still OOS; greeting/help
   replied naturally. This falsified description-only repair of that terminal.
3. Removed redundant model OOS tool: EN thanks, greeting and unavailable flight
   booking replied naturally; CN closure unnecessarily delegated as information-only.
4. Defined delegated work as requiring actual domain investigation: CN closure
   replied naturally; EN thanks and CN no-operation clarification unnecessarily
   delegated as information-only; address request correctly delegated for preparation.

No planning_requires_action in these probes; final text was accepted in one model
call, reasoning blocks were separated by SDK tests, and mixed business calls kept
their preamble out of result delivery. No visible drafting prose observed in these
small live samples. This does NOT establish universal absence of drafting prose.

Remaining finding: a model can still choose unnecessary delegation even when its
own objective says no action is needed. Do not add a regex against objective text,
another response tool or an online classifier to hide that failure. This is a
semantic routing-quality gap, not a malformed native text result. Keep the live
failures in the record and assess the full planning input/task-selection behavior
before declaring conversational efficiency or task22 closed. Official task22
scores from the previous run remain0; this repair did not rerun that task.

Steps: owner/consumer migration done; bounded protocol/PG tests done; independent
review done; live author probes done with semantic failures preserved; scoped
commit/push follows this report. No full tau task rerun. No claim that all
historical business tasks passed.
