# Bound interaction component replay — 2026-09-07

Scope: five fixed synthetic composition/verification cases; no business tools,
Publication, database outcome grading, or live source validation.
Input is a new fixture adding runtime-owned task/field bindings to the prior
five cases; historical artifacts are not rewritten.

| Case | Observation | Assessment |
|---|---|---|
| Known target, missing refund choice | Asked refund destination, no extra permission | PASS |
| Genuine target ambiguity | Asked which of two orders | PASS |
| Prepared action approval | USD 80, original card, target and unsubmitted action stated | PASS |
| Known goal, missing delivery choice | Asked original/new address | PASS |
| Pure permission as missing input | Composer invented a need to reconfirm the order | REJECT, bound rejected task returned |

The last case is intentionally not a valid user interaction. Rejection enables
the existing runtime to return feedback to the domain task; this replay alone
does not demonstrate successful domain repair. That transition and bounded
retry are checked separately with graph/SDK and PostgreSQL tests.

Prior adequate-approval false rejections remain in earlier reports. One PASS
does not establish a stable false-positive rate. No benchmark aggregate reward
or customer completion rate is claimed here.
