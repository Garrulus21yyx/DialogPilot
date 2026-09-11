# Tau3 task20 automated analysis

## Outcome

- Session: `tau3-c55ea725553f4249a9b1416155294020`
- Official reward: `0` (`termination_gate_only`)
- Business status: `COMPLETED_UNSCORED`
- Quality status: `WARN`
- Root-cause status: `VERIFIED`

The approved `modify_pending_order_items` write committed under receipt
`official-call:tau3-call-26f306148d9d463c9756131549e1ee09`. The selected final
publication records `task_completed=true`, but it landed at Tau step 80 of 80.
Tau stopped before the user simulator had one more turn to emit `USER_STOP`, so the
official database and action evaluators were not run.

## Replay scopes

The trajectory contains 25 calls across the six repeated query signatures: five
calls for the same order, four for the same user, and four calls for each of four
products. After the first call in each signature, 19 calls are exact repeats.

Those 19 calls do not have one fully proven root cause. The causal ledger exposes
two distinct mechanisms:

- Cross-continuation reset: 11 `READ_OBSERVED` events reused an identity from an
  earlier work item but were classified `ALLOW_NEW_EVIDENCE`.
- Same-work-item replay: 5 `READ_REPLAYED` events were allowed after compaction via
  `ALLOW_FIRST_REPLAY` or `WARN`.
- The guard also emitted 2 `BLOCK` decisions; these are blocked attempts, not
  automatically counted as executed duplicate tool calls.

The causal events do not yet join the original business call, the internal historical
observation read, and the next business call emitted by the publication. Therefore
event counts cannot be asserted to equal or partition the 19 external duplicate
calls, and any remainder stays unattributed rather than being assigned by subtraction.

The verified roots are correspondingly scoped:

1. `READ_REUSE_CONTRACT_NOT_ENFORCED_AFTER_COMPACTION` covers allowed same-work-item
   replay events after working-context compaction.
2. `READ_NOVELTY_AUTHORITY_SCOPED_TOO_NARROWLY` covers known read identities admitted
   as new evidence across continuation work items.

Together the exact repeats consumed 38 Tau steps and left no turn for `USER_STOP`,
but neither scoped root is claimed to explain every one of the 19 calls.

## Trace and usage

- Langfuse traces: 58
- Observations: 500
- Causal events: 50
- Generations with usage: 43/43
- Input tokens: 475,369
- Output tokens: 10,930
- Cache-read tokens: 290,176
- Provider total tokens: 776,475
- Compactions: 8
- True errors: 0
- Expected status events: 2

The machine-readable evidence, event IDs, execution chain and clusters are in
`inspection.json`. The raw conversation is in `task-20-trajectory.json` and the
checkpoint projection is in `task-20.json`.
