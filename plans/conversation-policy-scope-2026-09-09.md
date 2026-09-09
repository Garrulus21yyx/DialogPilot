# Conversation policy audience boundary

Observed: task22 operation-scope rerun turns 6–7 selected RESPONSE, not delegation,
despite correctly identifying account-only reversion. Langfuse generation
83e5fcab1023d918 (trace b136097f8de9fe128ea195f6f5880242) contains both coordinator
instructions and the entire retail executor policy in the system message.
This establishes conflicting role attribution, not proof of a unique stochastic
model cause. The reply layer also injected the same executor policy.

Owner repair: AgentDefinition now separates explicitly authored conversation_policy
from business_policy (specialist execution). Planning and response evidence consume
conversation_policy; specialist/runtime preparation review retains business_policy.
Registry descriptions route capabilities; action semantics and actual evidence
remain the source for business explanations. There is no runtime policy extraction,
extra LLM, fallback injection of executor text, or change to execution authorization.

The retail environment adapter supplies identity, privacy, evidence and interaction
constraints for conversation, while retaining the original full environment policy
for domain work. This is environment-level configuration, not a task22 prompt rule.
An absent conversation policy supplies no extra constraints, not the executor policy.
Policy authors must explicitly maintain this scope when adding environments.

Consumers migrated together: planning payload, SDK system context, reply author,
reply verifier's evidence snapshot, and their contract tests. Registry dataclass
serialization/fingerprint includes the new field; existing checkpoint/bundle identity
checks remain active. No old pending task is re-executed or rewritten by this change.

Acceptance: arbitrary execution-policy text cannot enter planning or reply evidence;
conversation constraints survive; domain policy remains intact; author and verifier
share the same snapshot including repair; policy changes affect registry identity.
Broader attribution tests still reference the removed adapter `_approval_decision`
and expect a read-completion reviewer no longer present. Those pre-existing test
contracts are reported separately, not repaired by restoring redundant runtime code.

Non-goals: rewriting routing, adding a new approval mechanism, changing simulator
instructions, or claiming that policy isolation alone guarantees model delegation.
Live task22 is not rerun as part of this repair; contract tests and real model
behavior are reported separately.

## Validation and delivery

Implemented; targeted contracts verified, live behavior not yet revalidated.
136 planning/response/environment-binding/context tests passed; 42 approval and
framework-agent tests passed with TEST_DATABASE_URL, including subprocess checkpoint
recovery (one existing fork warning). git diff --check passed.
Broader attribution checks exposed the pre-existing obsolete expectations described
above; not reported as passing. No changes to those tests or to user-owned edits.
Next: compare task22 post-completion reversion planning with the new audience-scoped
input; preserve any remaining failure separately from the confirmed injection defect.
