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

## Deployment and retirement — completed follow-up

User now authorizes actual migration and removal of superseded paths. Base 434d346;
unrelated RCA changes remain excluded. Running compose app is older than the current
branch: database 0029, old image without LangChain. Inventory: 15 conversations,
28 canonical events, no missing synchronous inbound links, no Redis summary keys;
15 raw Redis windows still feed fact extraction and are not disposable summaries.

- done: inspect pending migrations/old stores; take and restore-test backup.
- done: remove production Redis-summary fallback and redundant projection branch.
- done: build committed application, migrate database, cut over and verify health.
- done: remove retired live storage/mounts after verified archival. No unrelated
  queue/platform work or paid model evaluation.

Deployment evidence (2026-09-11):

- Live database was 0029. Verified its old migration ledger before writing. Empty
  old flow-state table confirmed before the destructive 0035 schema retirement.
- Stopped old app before backup/migration. PostgreSQL custom dump restored into an
  isolated verification database, then successfully upgraded 0029 -> 0038. All four
  archived SQLite files passed `PRAGMA integrity_check`.
- Applied the same owned migration chain to the actual compose database. Live head
  20260911_0038; ledger SHA256
  `b0dcce041371c9588160d8505cf2d7c37aac7ddca55ac379184f0ec8e1930c1b`.
- Deployed committed code `ac3385fcb0d5bf2427ab8486bacc7f22cb85154a`, Docker image
  `fce1951b0a52`, built from git archive (unrelated dirty RCA files excluded).
  LangChain/LangGraph 1.2.11 import checks passed without network. Production build
  now skips optional ML dependency stage even on the legacy Docker builder; removed
  obsolete SQLite directory creation from both image targets.
- Application Docker health `healthy`; direct API and Nginx `/health` HTTP 200.
  Canonical counts remain 15 conversations / 28 events. Live canonical read,
  cache fill and cache hit produce equal contexts; cache presence verified.
- Deleted unmounted volumes `dialogpilot_dialogpilot-badcases`,
  `dialogpilot_dialogpilot-customer-operations`, `dialogpilot_dialogpilot-react-runs`,
  `dialogpilot_dialogpilot-evolution`. No customer order/refund/security rows existed
  in old SQLite. Archived 35 terminal ReAct runs (33 completed, 2 tool_error), 5 old
  badcases + 39 learning records, 4 old bundles + 2 rollouts; these historical formats
  are archival, not silently converted into current runtime checkpoints or PG records.
- No old Redis summary keys existed. Kept the 15 working inputs still used by fact
  extraction. The application no longer mounts the four retired volumes and cannot
  fall back to the Redis summary through the public finalize endpoint. The fact
  adapter accepts only working-window and fact-scheduling projections.
- Restore verification database and temporary container dump deleted after validation.
  Protected archive: `/home/yang/dialogpilot-migration-20260911-P9pMhp` (owner-only).
  PostgreSQL dump SHA256:
  `2af79eb878b4a2ad8f9eff2d8fdf9c3e68b24e71eb2e35178e0c9ca3a5325f4c`.
- Follow-up tests: 15 passed (retirement, real PG projection, API lifespan).
  Independent reviewer confirmed production consumers retained and migration order;
  identified a test import error, corrected before the successful selection.

Recovery is manual from the protected archive into an isolated/restored database,
not an automatic fallback to an old runtime against schema 0038. The active container
has one current Target chain. No load-test, adaptive throttling or broker deployment
is claimed by this migration.
