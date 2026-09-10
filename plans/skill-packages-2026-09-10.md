# Optional domain Skill packages

## Contract and scope

SkillManager owns immutable, versioned instruction-package snapshots. Only
`*/SKILL.md` declares a package; references are resources, not new capabilities.
Domain workers discover descriptions and read selected resources through the
existing LangChain tool loop. Package content grants no tool/action permissions
and produces no business evidence. Existing executable product identification
continues through CapabilityRegistry and its executor.

API and runtime share one manager. Reload affects subsequent invocations;
an invocation reads one snapshot. Unavailable versions return typed tool errors.
No keyword injection, extra planner, summary call, approval state, or runtime.
Retire the four disconnected legacy role prompts; roles and execution policies
already belong to AgentDefinition. First package: product-upgrade comparison.

## Progress

- done: traced loader, API, composition, framework tools and existing executor.
- done: replaced disconnected loader, connected lazy package reads, removed four
  stale role prompts and obsolete prompt-size configuration.
- done: documentation and tests; 106 passed with real PostgreSQL on 2026-09-10.
- in_progress: scoped commit/push; unrelated working-tree changes excluded.

## Acceptance

Metadata only before read; references not independently discovered; snapshot
version covers resources; traversal and cross-agent reads rejected; tool/action
allowlists unchanged; ordinary framework ToolMessage history carries reads;
old execution Skill still works. Tests do not attest real-model business gains.
Before a model comparison, preregister fresh multilingual cases and fixed budget;
do not reuse the repeatedly debugged task20 as held-out evidence.

## Validation evidence and limits

Ran test_skill_packages, test_target_framework_agent, test_lifespan,
test_target_product_http_postgres_e2e, test_product_target_runtime,
test_agent_instructions and test_target_context_compaction with TEST_DATABASE_URL.
106 passed; one existing fork/multithreading deprecation warning. Initial startup
test failed because its old ToolManager stub omitted tools_for_agent and its tool
list omitted read_conversation_observation. Updated that fixture to the current
interface and added the shared SkillManager identity assertion; production tool
semantics unchanged. git diff --check passed.

Native framework tests prove the guide becomes working history without becoming
a FactRecord, and ordinary business tools retain their allowlist. Snapshot tests
cover resource changes, reload failure, invocation isolation and out-of-package
reads. These are integration guarantees, not a measured model-quality uplift.
No paid model or τ³ business task was rerun.
