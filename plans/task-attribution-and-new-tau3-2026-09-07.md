# Task attribution and two new τ³ tasks

Status: implementation and two fixed task runs complete; official total evaluation unavailable because judge credentials are missing. Business follow-ups remain open. Scope: diagnosis continuity, then two fixed new retail development tasks. Do not change task answers, planner behavior, business permissions, or score semantics.

## Cause and contract

The evaluation approval classifier uses the shared structured-model caller without the configured SDK callback. Its decision survives, but the model observation and correlation do not. Domain execution catches exceptions to preserve partial results, but the conversion to AgentResult retains only a reason string. The conversion owner must retain safe exception details without changing the terminal state or losing completed evidence.

Target contract: approval request/result/failure uses official callbacks and session/turn/approval identity. Every caught domain execution exception retains stage, original typed cause chain, retryability and work identity in existing execution_feedback. Control cancellation remains a control outcome. Existing framework tracing, persistence and recovery consume this same result; no additional event bus, exception protocol or fallback runtime. Diagnostics are not business evidence or public replies. Missing traces are reported as missing evidence, not guessed causes.

## Steps

1. done — repair owners and inspect all affected result/trace consumers. Also wired the missing production domain callbacks; archive-save failures retain each call's cause chain.
2. done — 107 tests passed with PostgreSQL, including actual composition, SDK in-memory export, partial evidence, parallel archive failures and checkpoint serialization. Independent reviewer found no blocker in the bounded attribution scope.
3. done — implementation committed/pushed as 89cf2a4. Prior manifests contain only task IDs 0/1; train offsets 2/3 (IDs 2/3), seed 300, max_steps 80, completion floor 4096, simulator max_tokens 512, existing role models. One run, no task replacement or success selection.
4. done — both simulations user_stop; one write per task; official ENV strict replay DB match 1/1 each. ALL judge failed on missing OpenAI credentials. ACTION diagnostic 0 for a missing reference read, not a missing write. Raw artifacts preserved; deterministic scores computed from saved trajectories without application rerun.
5. done — report at artifacts/eval/tau3-new-dev2-2026-09-07-attribution-v1/REPORT.md. Cloud full pagination verified approvals/domain tracing. Fixed duplicate SDK handlers at sink owner and isolated independent evaluator errors. 112 tests passed, no skips, one pre-existing fork warning; independent fresh reviewer approved both changes. Commit/push includes code, tests and evidence.

Remaining business issues: duplicate request_user_input/approval confirmations in both tasks; task 2 model chose report_blocked with “already completed” reason, leaving task_completed=false. Not fixed or claimed closed by this attribution task. Dollar-cost mapping warnings and missing official judge credential are separate environment/measurement issues. Old cloud spans contain duplicate telemetry; counts are not unique model/tool calls. New nested-parallel SDK export test proves one observation per actual invocation under the shared handler.

No claim of full-benchmark quality from two development tasks. No reinterpretation of masked historical records as exact replays.

Task 2: count currently available T-shirt options and return a cleaner, headphones and smart watch. Task 3: count T-shirt options and modify pending small T-shirts to purple while retaining size/neckline and preferring polyester. Task scenarios are evaluation inputs; expected actions/answers are never supplied to the application. Unrelated pre-existing dirty files are left intact and source fingerprints are recorded.
