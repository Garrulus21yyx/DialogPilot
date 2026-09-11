# Backend capacity and recovery validation — 2026-09-11

Scope: existing Target admission/cache/source/model/Worker boundaries. No paid
model requests, new queue service or alternative runtime. No database schema
migration is needed for this change.

Final scoped regression: **172 passed, 0 failed, 0 skipped**, 212.602 seconds.
Raw evidence: [scoped-regression.xml](scoped-regression.xml).

| Real-service scenario | Requests | Full source reads | Peak source reads | Rejected | Accepted P95 |
|---|---:|---:|---:|---:|---:|
| One hot context expires | 40 | 1 | 1 | 0 | 37.35 ms |
| Distinct contexts expire | 40 | 4 | 4 | 36 | 41.48 ms |
| Redis connection refused | 40 | 4 | 4 | 36 | 43.26 ms |

These tests deliberately configure a four-read limit and burst all requests at
once. Rejection is explicit overload handling, not a successful business answer.
One full source read uses seven PostgreSQL transactions including revision fences;
the other scenarios use 28. These are local measurements, not production SLOs.
Redis fault uses a disconnected client; the shared Redis service is not stopped.
Each PostgreSQL test uses an isolated database.

Additional verified properties:

- Shared Redis quotas across limiter instances; tenant/user isolation; HTTP 429
  versus dependency 503 before durable acceptance.
- PostgreSQL native waiter bound and statement timeout; lock contention becomes
  typed HTTP capacity failure and leaves no accepted invocation behind.
- Twenty concurrent durable admissions with capacity four accept exactly four;
  replay of an existing accepted identity remains valid at capacity.
- First-claim queue expiry does not discard a previously started recovery.
- Model concurrency rejects before sending; SDK transport retry sends three
  requests total at max_retries=2, not multiplied by structured-output retries.
- Kill Worker before/after write: a second Worker restores the same invocation
  from PostgreSQL LangGraph checkpoint and finishes with exactly one refund.
  Stale owner completion is rejected; duplicate writes are zero in both cases.
- Canceled cache waiters do not release still-running source capacity; results
  are copied per caller. Existing revision/deletion fences continue to pass.

Process recovery is an integration test of the native checkpoint, Worker and
governed write boundary, not a model-driven full HTTP task evaluation.

## Preserved failure and limits

The broader exploratory run is retained in
[broader-regression.xml](broader-regression.xml). Its old six-scenario HTTP fixture
emits the retired `execute_refund` planning goal and has a tool double without the
current `refresh` argument. It was not made green by reintroducing compatibility
goals. Current product HTTP and registered write-recovery tests pass in the scoped
suite. This report does not assert the entire repository or historical tau tasks
are passing.

Independent fresh-context review checked owner placement, typed timeout handling,
quota counter governance and same-invocation checkpoint recovery. No additional
substantive defect remained in that review. Per-process limits must be budgeted
across replicas; these changes do not create a distributed model semaphore.
