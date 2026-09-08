# Action-scoped reply evidence: bounded development replay

Date: 2026-09-08. Source: fixed ten-task batch, task 11, Langfuse observation `5068a2cc4eee3d27`.

## Contract

A committed receipt proves its operation, while a pending proposal describes its own unexecuted operation. A reply may report one completed action and ask approval for another. Response evidence now pairs receipts with original WorkItems by operation key, not reused turn-local IDs; copied receipts without that contract remain unassociated rather than acquiring the current proposal's arguments.

## Experiment

One exact archived question/answer/evidence replay, then two answer-only negative mutations. Configured verifier: `deepseek-v4-pro`, output budget 4096, zero SDK retries. Exactly three model calls, no business tools or writes. Archived evidence was not enriched with the new associations: this isolates prompt changes, not the full production change.

| Candidate | supported | answered | approval terms complete |
|---|---|---|---|
| Original completed-first/pending-second reply | true | true | true |
| Falsely claim pending action executed | false | false | false |
| Falsely claim refund funds already arrived | false | false | false |

Important remaining error: the third rejection's explanation still misdescribed the first committed return as merely prepared. Its final rejection is appropriate, but its attribution is not fully correct. Do not count this as semantic closure.

## Supporting checks and limits

- 203 tests passed with PostgreSQL enabled; 12 existing trusted-context serialization warnings.
- Eight structural combinations cover same/different targets, repeated/different local IDs and copied/noncopied receipts.
- Independent source review found no blocker in this scoped association change.
- Raw observation/request/response files are retained locally beside this report; no credentials are included in the report.
- Developer probes, not held-out accuracy. No fresh customer E2E success claim, no official reward change. Overall approval/dialogue/state convergence remains open.
