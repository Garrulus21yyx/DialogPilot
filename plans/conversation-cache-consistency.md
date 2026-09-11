# Conversation PostgreSQL / Redis consistency

Base: 104881d. Existing RCA edits excluded.

Contract: PostgreSQL owns committed conversation and summary. Redis caches an exact
versioned snapshot, never independently appends or summarizes the Target input.
Every read checks authoritative scope, event/summary generation+version and deletion
epoch. Cache misses/outages load PostgreSQL; stale entries cannot become current.
Publication/admission continue their existing PostgreSQL transaction/outbox paths.
No distributed transaction or second memory runtime.

Plan:
1. implemented: trace owners and implement version-fenced snapshot cache.
2. implemented: wire shared Redis lifecycle, deletion and Target reader; register the cache location.
3. verified: PostgreSQL/Redis, migration and lifespan checks; scoped commit/push follows.

## Root cause and ownership

The previous Redis working window and PostgreSQL summary/transcript had separate
coverage coordinates and update timing. A window's age or length cannot certify
summary coverage. HEAD 104881d removed that reader dependency; this change restores
Redis as a cache of that same canonical result, not as a second history producer.

Admission/Publication own the PostgreSQL event sequence. ThreadSummary owns its
generation, version and covered range. The context reader owns assembly. Redis owns
no semantic decisions. A single envelope includes scope, excluded current request,
event/deletion versions, summary versions and projection versions/watermarks.

Read protocol: source revision -> matching cache or canonical source -> source
revision again. Concurrent changes restart the read at most once. Continuing change
returns CONTEXT_SNAPSHOT_CHANGED; source unavailability returns a typed UNAVAILABLE,
never an unchecked cached answer. Redis outage/corruption is a cache miss.

## Deletion and cancellation

Cache fill runs under the existing conversation row's shared lock; deletion's row
update is exclusive. The complete guarded fill runs in a worker thread, so caller
cancellation cannot release the SQL lock while its Redis write is still running.
Redis SDK WATCH/MULTI/EXEC fences delayed or unknown writes. Deletion replaces the
key with a short-lived, content-free tombstone; a delayed EXEC watching the old key
aborts. WatchError discards the fill, without replaying the stale write. The existing
deletion outbox retries deletion failures. Both content and marker have a 300s TTL;
TTL is cleanup, not an authorization/freshness decision. Shared Redis connections
have explicit two-second connect/read timeouts.

## Scope, alternatives and tradeoffs

- One PostgreSQL semantic read path, optionally accelerated by a certified cache.
  No dual write transaction or new queue. Cache misses do not select a legacy runtime.
- Validation costs PostgreSQL revision reads; cache hits avoid transcript/summary
  loading but not authority checks. No throughput/hit-rate improvement is claimed.
- Cache identity includes request exclusion, so new turns normally miss. This is
  intentional correctness; cross-turn cache optimization is not this work item.
- Existing working-window/fact-extraction projections remain for their own consumers;
  they do not supply the Target conversation's model history.
- New synchronous admissions now include the canonical inbound turn key, like durable
  admissions. Earlier synchronous events missing this key are NOT rewritten here;
  that separate historical repair remains explicit. Production durable admissions
  already contain the key. No legacy checkpoint/data conversion was attempted.
- Migration 20260911_0038 registers the new ephemeral cache location and deletion
  proof, without changing or deleting business records. Apply before deploying this
  version. This turn migrates isolated test databases only, not the running service.

## Acceptance evidence

Initial source/cache/deletion/Target tests: 51 passed using real PostgreSQL and Redis.
Final combined suite: **96 passed in 180.55s**, no skips. Includes real PostgreSQL
and authenticated Redis, all new cache tests, memory projection, deletion dispatcher,
admission, Target context, data-location registry, forward/concurrent migrations,
ThreadSummary, schema registry and API lifespan. `git diff --check` and compileall
passed. Independent final review: 8 additional governance/schema checks passed
(overlapping the combined suite, not additive). Cache contract verified within the
scope above; no deployment or historical synchronous-event migration claimed.

An intermediate expanded run caught missing
schema registry transition declarations; these were fixed before the final rerun.

Independent fresh-context review found and drove closure of late-fill and unknown
Redis write/delete races. Review confirms SDK watched transactions do not retry an
old EXEC on connection error. Tests cover scope/revision permutations, current-input
exclusion, corrupt/outage cache, stale fills, concurrent commits, deletion/cancel,
summary commit, new turns and a real Redis watched transaction invalidated by deletion.
No paid model calls or business benchmark reruns were used.

Delivery: scoped changes committed on feat/customer-service-target-architecture;
unrelated RCA worktree edits excluded. Deployment must run the existing
scripts/run_postgres_migrations.py for 0038 before restarting the service.

## Deployment and retirement — active follow-up

User now authorizes actual migration and removal of superseded paths. Base 434d346;
unrelated RCA changes remain excluded. Running compose app is older than the current
branch: database 0029, old image without LangChain. Inventory: 15 conversations,
28 canonical events, no missing synchronous inbound links, no Redis summary keys;
15 raw Redis windows still feed fact extraction and are not disposable summaries.

- in_progress: inspect pending migrations/old business stores; take restorable backup.
- pending: remove production Redis-summary fallback and redundant projection branch.
- pending: build committed application, migrate database, cut over and verify health.
- pending: remove only retired live storage/mounts after verifying its disposition;
  record recovery path and push delivery evidence. No unrelated queue/platform work.
