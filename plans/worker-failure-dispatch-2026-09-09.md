# Worker failure dispatch

Base: 9a50ea1. Preserve unrelated dirty files, especially framework-agent changes.

Observed mechanism: framework workers already produce timeout/provider/no-progress
outcomes, but TurnRuntime only observes assignment repairs and explicit reads.
Unhandled read/worker exceptions escape the parallel WorkPlan wave. Thus typed
failure and planner recovery are disconnected, not a missing second scheduler.

Contract:
- Runtime contains non-writing worker exceptions as typed outcomes with causes;
  cancellation and framework interrupts propagate. Write failures remain owned by
  the operation ledger/reconciliation runtime; never blindly repeat writes.
- SDK owns transient model transport retries, bounded; no whole-Agent retry loop.
- Existing observation graph presents actionable read/delegated failures to the
  Conversation Agent, with original objective, diagnostics and successful peers.
  Same progress/budget limits stop repeated failed plans. Waiting approvals/inputs
  are not failures. Completed unrelated work is retained, not rerun implicitly.
- Programmer/configuration failures are terminal, not automatic retry candidates.
- Planner failure still delivers available results; no silent success or reset.

Steps:
1. done: connected existing worker result and observation boundaries.
2. done: generated status matrix and integration failure/recovery/replay tests.
3. done: scoped review; commit/push follows. No paid model/τ³ run.

Implementation:
- OrchestrationRuntime catches escaped non-writing worker failures with exception
  type/chain in AgentResult; peers complete and hard dependants become blocked.
  Direct work gets its declared timeout. Framework Agent retains its existing
  streamed-progress timeout and native model/tool-loop budgets.
- Execution-progress policy exposes current retryable read/delegated failures and
  typed AGENT_NO_PROGRESS to the existing TurnRuntime observation phase. Both
  Manager and graph consume that same predicate. Retained failures and response-only
  plans do not retrigger it. Existing 4-step observation budget and stagnation
  guard bound repeated planning; no separate worker retry engine was added.
- Conversation context includes reason, retryability, original task and successful
  outcomes, with explicit decision responsibility. Unanswered waits and committed
  writes retain their original owners.
- SDK model transport retries: max_retries=2. No additional model middleware retry
  or whole-Agent replay layer. Agent budget/deadline still applies.
- Turn checkpoint version is v23-worker-recovery, no v22 continuation fallback.

Verification (scripted models/workers, real PostgreSQL where configured):
- Combined failure/progress/turn/observation/framework/control/write-recovery suites:
  **238 passed**, 63.68 s.
- Final failure + WorkPlan + SDK policy tests: **59 passed**, 5.13 s, including
  the direct deadline and current code. Counts overlap, not additive.
- Both memory and PostgreSQL cover connection/no-progress × recovered/report/
  planner-failure/repeated-failure; recreated runtime replay does not repeat tools.
- Status matrix excludes writes, waiting, cancellation, terminal faults and retained
  history from automatic replanning. Cancellation and escaped write faults are not
  converted into retryable outcomes; broken execution contracts still raise.
- git diff --check passed. Unrelated dirty files excluded.

Limitations: paid-model choice quality is not claimed. Escaped write infrastructure
errors still fail the Run for its existing durable takeover/ledger reconciliation;
Conversation Agent must not create a replacement write. This is intentional
ownership, not an ordinary read retry. No full repository/ecommerce closure claim.
