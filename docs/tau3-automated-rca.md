# Tau3 automated failure analysis prototype

This prototype analyzes saved tau3 runs without invoking a model. It establishes
objective violations and typed execution mechanisms, then emits causal hypotheses
with explicit missing evidence and a next probe.

```bash
python scripts/analyze_tau3_failures.py artifacts/eval/<run> \
  --output artifacts/eval/<run>/rca.json
```

The output keeps four concepts separate:

- `evidence`: an artifact-backed fact with a source and JSON locator.
- `violation`: an observed mismatch, such as an official action mismatch.
- `mechanism`: a directly demonstrated event, such as a missing write or a typed
  context-budget rejection.
- `hypothesis`: a causal explanation that still needs the listed probe.

`root_cause_status` remains `OPEN` until a finding at the `root_cause` layer has
been verified by a controlled replay or an equivalent owner-boundary test. A
verified mechanism alone does not establish why that mechanism occurred.

Batch application logs are summarized as `unbound_run_evidence`. They are never
assigned to a task unless the saved event contains a stable task, turn, or trace
join key. This prevents one task's provider or context failure from contaminating
the attribution of neighboring tasks.

The first calibrated witnesses are:

| Task | Expected analysis |
|---|---|
| 8 | Missing required write; stale goal scope is a supported hypothesis |
| 13 | Write argument mismatch; simulator goal drift is a supported hypothesis |
| 20 | Missing required write plus context-budget and citation mechanisms |

The next instrumentation step is to persist `task_id`, `turn_id`, `goal_revision_id`,
`work_item_id`, `proposal_id`, `approval_id`, `tool_call_id`, and `receipt_id` on the
same Langfuse trace lineage. That lets a later diagnostic agent test ownership
hypotheses without guessing from temporal adjacency.
