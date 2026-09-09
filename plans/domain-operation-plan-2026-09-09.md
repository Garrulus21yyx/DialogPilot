# Domain operation planning

Status: implementation complete; local verification recorded below; semantic
closure OPEN. Extends R6 in the ten-task causal audit; does not close R7.

Failure: a domain proposes a locally legal write whose effect defeats another
requested change. An executable task DAG exists outside the domain, but the
domain's next-action contract does not expose its remaining operation plan.

Owner: the domain owns local feasibility reasoning. Its existing semantic review
checks that reasoning against tool contracts, evidence and the complete assigned
goal. The preparation boundary owns conversion to one approved executable action.

Contract: a prepare call may include an operation_plan of remaining operations,
their targets, prerequisites, effects and dependencies, selecting one ready step.
For multiple related writes this plan is required by semantic acceptance. Single
actions retain their existing concise call. Unknown prerequisite or incompatible
goals lead to evidence collection or the existing request_user_input, not invented
ordering. The plan is a model proposal, never authoritative state or permission.
Only the selected action is prepared; other nodes are neither queued nor approved.
Continuation uses receipts/current evidence to revise the remaining plan.

Implementation steps:
1. DONE: Add one bounded proposal contract and standard-library DAG validation.
2. DONE: Integrate the existing prepare wrapper and outcome review; strip planning
   metadata before business argument preparation/operation identity.
3. DONE: Verify malformed/valid graphs, selected readiness, no unauthorized steps,
   no automatic future execution and normal single-step behavior locally.

Non-goals: new scheduler, symbolic policy engine, commodity-specific rules,
extra model review, paid model or tau rerun. Structural tests cannot attest
model feasibility accuracy. R6 remains semantically OPEN pending fresh evidence.

Implementation: application/operation_plan.py owns JSON schema and graph validation
(jsonschema + graphlib, not a new scheduler). target_framework_agent.py exposes the
metadata and strips it before TargetActionPreparation. target_agent_middleware.py
uses the same validation before semantic review and the existing rejection budget.
target_domain_outcome.py reviews full coverage and state feasibility in its existing
call. Single operations do not acquire an additional model call or mandatory graph.
The model/reviewer, not a keyword heuristic, decides whether writes are related and
whether a missing plan is an error. This is an explicit semantic limitation.

Working messages/checkpoints retain the original proposal. Pending action contains
only the selected business arguments; no future plan node is auto-scheduled or
approved. On continuation, prior receipts and evidence ground the remaining plan.
Adapter version v7-operation-plan uses the existing old-checkpoint version boundary.

Local proof: tests/test_operation_plan.py includes all64 ordered four-node DAGs
with each selected node (256 readiness checks), malformed/schema/cycle/permission
rejections, existing bounded correction, conflict-to-input handback, metadata-free
operation identity, checkpoint codec roundtrip, and post-receipt SDK continuation.
Scripted review demonstrates decision handling, not model conflict recognition.
Independent fresh-context review found and verified the diagnostic privacy boundary:
schema values and cycle IDs must not enter exception diagnostics; original scoped
working records are preserved. No real user simulator/business write was invoked.

Final local/component/PostgreSQL run: 192 passed, including 19 operation-plan
tests. One pre-existing multiprocessing fork deprecation warning. Independent
review found no further definite execution-owner blocker; cycle exception IDs were
also removed from diagnostic chains and covered by a canary test. `git diff
--check` passed. No paid model/held-out semantic evaluation: multi-goal coverage,
correct effects, target binding and conflict recognition remain model judgments.

User-authorized ten-task regression subsequently attempted; see
`tau3-operation-plan-ten-rerun-2026-09-09.md`. Only19/20 have complete scoring;
21 is partial and the provider's HTTP402 prevents the remaining runs. Task19
eventually chooses a compatible action but has repeated asks/prose failures;
20 regresses with no modification. This does not close semantic R6/R7 or prove
the new plan improves performance. No runtime changes were made during the run.
