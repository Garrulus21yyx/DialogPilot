# Proposal selection boundary probe

Date: 2026-09-08. Development evidence only; no business tools executed.

Original task16 observation `259cf968870b0958` had an assigned watch-return goal,
but proposed cancelling a different, already-cancelled order. Its dependency
receipt already established cancellation. The previous boundary skipped proposal
selection, allowing a fresh pending approval to obstruct the actual return.

The new PREPARE_ACTION request reconstructs that observation's task context,
policy, capabilities and candidate. This is not an exact replay of an assessment
that previously existed. Fixed prompt/input, one call per role, max4096 output,
zero retries:

| Model role | Observed result | Expected rejection |
|---|---|---|
| Actor-equivalent REACT / Flash | accepted=true | Failed |
| Existing VERIFIER / Pro | accepted=false, correctly identifies wrong objective | Passed |

Both raw requests and responses remain locally in `probe.json` and
`verifier-role-probe.json`. This supports explicit verification-role wiring;
it does not establish calibration, general model superiority or task success.

Production candidate uses the existing SDK middleware and bounded review budget,
before preparing the action. Preparation acceptance is not execution approval.
Existing authority, approval, receipt and publication owners remain unchanged.
Integrated ten-task validation remains required.
