# Conversation planning boundary

ConversationAgent owns natural conversation, context interpretation, direct reads,
knowledge requests, specialist delegation and decisions on already-bound inputs
or approvals. A clear business-change request is delegated with all known details;
the specialist collects missing business values and prepares the operation.

New cancellation, address change, refund and account freeze are no longer direct
ConversationAgent planning shortcuts. They remain business capabilities of the
domain/runtime. The main planner does not receive raw write-operation descriptions.
Applicable business policies remain available because they can constrain reads
and the scope of delegation. Domain workers retain tool-specific prerequisites.

Existing review_action, supply_input, continue_active_work and cancellation of a
conversation objective are unchanged. Runtime resumes the exact authorized work;
this change does not turn a user request into execution approval. Human handoff
remains the existing explicit coordination capability.

Orchestrator still schedules DIRECT read WorkItems and DELEGATED domain tasks;
ToolManager performs actual calls. No second planner, fallback engine or judge
was introduced. Reply assembly is separate: removing main preparation authority
does not by itself prove that every generated question or approval is correct.

## Delivery and recovery

Default registry planning shortcuts changed, so its fingerprint changes. Existing
suspended work with an older fingerprint remains subject to the existing mismatch
check. Do not silently rebind old approvals or replay writes; deployment must drain
or explicitly migrate affected waiting work. Checkpoint/state formats were not
rewritten. Existing read and approval execution components remain in place.

Acceptance includes delegated changes without complete fields, preserved simple
reads and conversation, partial input, approval plus independent requests, and
registry-level write guards. Model probes are development evidence, not task22
business completion or a measured guarantee against repeated confirmation.
