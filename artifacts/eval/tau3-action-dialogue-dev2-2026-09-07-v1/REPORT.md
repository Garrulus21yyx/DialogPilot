# Action dialogue convergence: first verification, not closed

2026-09-07, implementation 0256231. Original development tasks 2/3, seed 300, one run each. No selection of best attempts; this directory is immutable run evidence. ALL remains unavailable because the configured official judge lacks its credential; ENV/ACTION are independent official strict-replay checks.

| Task | ENV | ACTION | Observed result |
|---|---|---|---|
| 2 | 0 | 0 | No return write. Initial planner omitted optional action scope; goal became read-only and later reported unavailable. |
| 3 | 1 | 0 | Initial modification goal also reported unavailable. User's further request triggered a new plan with action scope, one formal approval and one committed modification. Final resumed action/domain results both SUCCEEDED. |

Neither is a clean first-pass conversational success. Task 3's eventual success does not erase the earlier incorrect capability response. Original scores/trajectories are retained, not modified after diagnosis.

## Causal evidence and contract correction

Task 2 Langfuse session `tau3-308efa8a20e441f7befdf8465d1eeed3`, planning generation `c51ce3d65d84a513`, omitted `allow_action_proposals` from both goals. The initial return WorkItem `work:2:return_items` consequently had no allowed actions; continuation preserved that scope correctly. This distinguishes missing initial planning scope from a recovery scope loss.

The planning input listed read tools and skills but omitted Registry actions, while the provider instructed the model to use only the supplied cards. Additionally, the new scope field was optional and absence silently meant false. Correct the shared producer/consumer contract: cards expose registered action proposals; each open delegation explicitly states its scope; absence is invalid planning output rather than a hidden change of objective. Internal callers can still default to read-only; no user text heuristic grants execution authorization.

Task 3 session: `tau3-cfcf794571b54587ab27382ea81d8adb`. Its final successful resume supports the bound ToolMessage outcome repair, but does not establish universal model reliability. Further validation must use a separately identified run after the contract correction; this failed verification remains reported.
