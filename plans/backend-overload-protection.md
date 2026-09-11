# Backend overload protection

Status: implementation and scoped validation complete. Scope: Target API admission, conversation cache/source,
shared model boundary, existing durable Worker. No new queue or runtime.

## Contract and causal model

Cache correctness does not bound work. Every cache read first consults PostgreSQL;
concurrent misses and Redis failure can multiply source work. A finite connection
pool alone leaves unlimited waiters. Existing durable claims prevent duplicate
ownership but do not by themselves bound HTTP admission or queued work.

Owners: authenticated API admission owns tenant/user request allowance; cache
reader owns same-input coalescing; PostgreSQL pool owns connections, waiters and
statement timeout; model SDK boundary owns model concurrency and transport retry;
existing admission/store/Worker own durable acceptance and fenced execution.

Supported behavior: identical in-flight context reads share work; distinct reads
have finite admission; cancellation does not free capacity before underlying work
ends; revision/deletion fences remain authoritative. Overload returns typed retryable
failure, not stale evidence or an accepted task that was never persisted. Write
recovery continues to use original operation identity and receipts.

## Steps

1. done — inspect owners, supported errors and existing tests.
2. done — wire bounded protections at owners, migrate consumers/config.
3. done — tests: hot key, mass misses/expiry, Redis outage, Worker reclaim and
   duplicate commit fencing; include cancellation and tenant isolation.
4. done — final regression passed (172 tests); evidence saved in
   `artifacts/eval/backend-protection-2026-09-11/`. Git delivery is verified separately
   against the remote commit; this report is not a deployment attestation.
   No paid model evaluations.

## Exit evidence

Report actual downstream calls, peak concurrency, rejects, latency and recovery
observations. Distinguish deterministic local load tests from live-service fault
tests. Existing cache consistency and approval/recovery tests must still pass.
Unrelated attribution working-tree changes are excluded from commits.

## Implementation and boundaries

- `ConversationContextCache`: 300–360s TTL jitter. `CachedConversationReader`
  coalesces identical in-flight inputs including revision reads, with 8 unique
  reads/128 callers per process by default; immediate typed busy rather than an
  unbounded wait list. Each caller gets its own result copy. Same pipeline without
  Redis; no alternative source contract. Source SQL runs outside the event loop.
- `PostgresPool`: SDK max_size plus max_waiting=20, acquisition timeout 5s;
  session statement timeout 15s/lock timeout 3s. Separate typed pool exhaustion
  versus rolled-back query timeout. Migration runner is not subject to these
  application pool settings.
- Authenticated `/chat`: Redis `limits` fixed-window quota, defaults 30/user/minute,
  300/deployment-tenant/minute. Identity comes from the verified principal, not
  the request body. Redis unavailable: 503 before acceptance; no alternate quota
  store. History/ACK/status reads remain available. Fixed-window boundary bursts
  are deliberate, not a claim of a sliding-window limit. Counters count denied
  attempts too; API retries can receive 429 without changing idempotency identity.
- New durable Target turns: one atomic admission decision across processes,
  max 1000 outstanding invocation identities across start/execution outboxes.
  Existing identity replays bypass queue capacity, not authentication/rate limits.
  First claim after 300s expires without business execution, through existing
  failure Publication; attempts after a previous claim keep recovery semantics.
- Target model calls: existing `invoke_model` boundary shares 8 no-wait slots per
  process, released on completion/cancellation; SDK alone owns transport retries
  (2), explicit per-attempt timeout 120s. Structured-output retry only handles
  output errors, not transport failures. Existing per-work budgets remain.
- Uvicorn production entry: 128 concurrent connections/tasks and backlog 128.
  Each replica has its own process/pool limits; operators must budget their sum
  against PostgreSQL/provider capacity. This is not a distributed semaphore.

## Operational counter governance

Chat quota counters are security/admission operational state, owned by the API,
not conversation content or a replacement memory store. Hashed identity is
pseudonymous, not anonymous. Values are counts only; TTL equals the configured
quota window (default 60s), no export into Transcript/checkpoint/backup workflow.
Conversation deletion does not reset abuse quotas or let a caller bypass limits.
They are outside conversation-subject content deletion; Redis cache restoration
may temporarily restore conservative quota counts, never business authority.
No raw content, credential, or additional subject record is created.

## Validation scope and remaining unrelated evidence

The scoped suite covers real PostgreSQL/Redis, authenticated HTTP admission,
queue capacity, cache fences, SDK transport retry, and same-invocation process
recovery using PostgreSQL LangGraph checkpoint plus the governed write executor.
The process-kill check is an integration proof, not a full model-driven HTTP E2E.
An independent fresh-context review checked ownership, exceptional paths and the
strengthened recovery fixture. Capacity decisions and wait durations use the
existing Prometheus registry with bounded labels and no user identifiers.

The broader exploratory regression also exposed an existing six-scenario HTTP
fixture using retired `execute_refund` planning goals and a tool double missing
the current `refresh` argument. This is preserved as a failure, not changed into
a production fallback or counted as passing. The scoped suite includes the
current product HTTP contract instead. No claim of whole-repository green status
or new tau benchmark accuracy is made by this delivery.

## Mature facilities reused (checked 2026-09-11)

- Psycopg pool: https://www.psycopg.org/psycopg3/docs/advanced/pool.html
  (`max_waiting`, acquisition timeout, built-in pool statistics).
- limits: https://limits.readthedocs.io/en/stable/ (Redis atomic counters and
  strategies; new pinned dependency instead of a custom rate-limit algorithm).
- Existing LangChain/Anthropic integration keeps native request parsing/retries;
  existing LangGraph PostgreSQL checkpoint and business receipt owner handle
  process recovery. No extra agent loop, queue broker, or persistence path.
