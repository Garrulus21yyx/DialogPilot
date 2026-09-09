# Author-only replay: failed native-history hypothesis

No business tools were bound or executed. Two model calls; no production changes.
Source is logged Langfuse input, with existing redaction preserved. Masked values
were not reconstructed. These are development cases, not held-out results.

## Findings

| Producer | Observed failure | What the captured input establishes |
|---|---|---|
| Main planner, cd42cc02de79d3da | Combines state clarification with permission for two changes | Already has native history and no-selected-approval instruction; no bound missing-input state |
| Composer, 51efc55e97af9e7f | Describes two requested changes as prepared | Exactly one pending action: modify_user_address:v1 |

## Controlled comparison

Same historical composer system and evidence, same current SYNTHESIS profile,
4096 output cap. Native-history variant moves recent messages to SDK native
messages and retains their provenance in the payload; no new facts or instruction.

| Variant | Input tokens | Output tokens | Result |
|---|---:|---:|---|
| Original single JSON | 10078 | 106 | Incorrectly requests approval for both changes |
| Native history | 10025 | 95 | Same scope expansion |

Results: comparison.jsonl. Capture utility: capture.py. Replay: compare.py.
The experiment rejects message-layout alone as a solution. It does not establish
why the model ignores the instruction, nor validate the current end-to-end chain.
No extra positive-control calls were run after both variants failed.
