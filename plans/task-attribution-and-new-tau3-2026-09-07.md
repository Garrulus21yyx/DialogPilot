# Task attribution and two new τ³ tasks

Status: in progress. Scope: diagnosis continuity, then two fixed new retail development tasks. Do not change task answers, planner behavior, business permissions, or score semantics.

## Cause and contract

The evaluation approval classifier uses the shared structured-model caller without the configured SDK callback. Its decision survives, but the model observation and correlation do not. Domain execution catches exceptions to preserve partial results, but the conversion to AgentResult retains only a reason string. The conversion owner must retain safe exception details without changing the terminal state or losing completed evidence.

Target contract: approval request/result/failure uses official callbacks and session/turn/approval identity. Every caught domain execution exception retains stage, original typed cause chain, retryability and work identity in existing execution_feedback. Control cancellation remains a control outcome. Existing framework tracing, persistence and recovery consume this same result; no additional event bus, exception protocol or fallback runtime. Diagnostics are not business evidence or public replies. Missing traces are reported as missing evidence, not guessed causes.

## Steps

1. done — repair owners and inspect all affected result/trace consumers. Also wired the missing production domain callbacks; archive-save failures retain each call's cause chain.
2. done — 107 tests passed with PostgreSQL, including actual composition, SDK in-memory export, partial evidence, parallel archive failures and checkpoint serialization. Independent reviewer found no blocker in the bounded attribution scope.
3. in_progress — commit/push implementation. Prior manifests contain only task IDs 0/1; freeze train offsets 2/3 (IDs 2/3), seed 300, max_steps 80, completion floor 4096, simulator max_tokens 512, existing role models. One run, no task replacement or success selection.
4. pending — run each new task once through existing real application/evaluator; save all results including failures, inspect Langfuse observations and business evidence.
5. pending — report official score, write result, final response, first observed failure and evidence gaps separately; commit/push report.

No claim of full-benchmark quality from two development tasks. No reinterpretation of masked historical records as exact replays.

Task 2: count currently available T-shirt options and return a cleaner, headphones and smart watch. Task 3: count T-shirt options and modify pending small T-shirts to purple while retaining size/neckline and preferring polyester. Task scenarios are evaluation inputs; expected actions/answers are never supplied to the application. Unrelated pre-existing dirty files are left intact and source fingerprints are recorded.
