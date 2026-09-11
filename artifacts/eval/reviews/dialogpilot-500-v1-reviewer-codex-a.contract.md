# Reviewer A contract: dialogpilot-500-v1

Reviewer ID: `codex-a-2026-08-30`

Scope: independent semantic review of the 500 proposed cases. This artifact
does not execute the system under test and does not grant human-gold status.

## Disclosure

This is not a pristine blind review. Before the user clarified the assignment,
the reviewer saw the dataset overview, the documentation claim that the current
Planner matches 120/120 routing expectations, and a small prefix of expected
intent labels. No prior LLM-review artifact was read. Planner output is not used
as routing evidence below.

## Decision meanings

- `accept`: the proposed expected result follows from the visible input and the
  bounded project contract.
- `revise`: a specific expected-result or input-contract correction is needed.
- `ambiguous`: more than one supported answer remains reasonable from the
  visible input; rewrite or human adjudication is required.

Confidence is reviewer triage metadata, not a calibrated probability.

## Intent contract

Review against the closed `IntentCategory` enum and its documented examples,
preferring the specific supported intent when the message supplies enough
evidence. Relevant boundaries for this subset are:

- `account`: profile changes and account closure.
- `account_security`: stolen credentials/devices/cards, unauthorized activity,
  identity verification, and sensitive-account protection.
- `logistics`: delivery, shipping, tracking, and arrival timing.
- `payment_issue`: declined/failed payments, duplicate charges, and explained
  payment fees.
- `refund`: refund eligibility, requests, and refund status.
- `technical`: non-login feature or system malfunction.
- `technical_login`: login/passcode/PIN authentication failure.
- `other`: outside the supported customer-support domain, not merely an
  unfamiliar wording of a supported intent.

Source labels are provenance evidence, not permission to resolve an ambiguous
standalone message using hidden source context.

## Routing contract

Choose the minimum complete set of task Owners from the visible request:

- `general`: order, logistics, membership, account, or general information.
- `technical`: login, error code, crash, or configuration troubleshooting.
- `billing`: payment, charge, refund, invoice, or subscription work.
- `account_security`: account takeover, suspicious login, identity verification,
  or sensitive-account protection.
- `escalation`: human handoff and escalation summary.

Each selected Owner has one task ID, `<owner>_task`. Negated evidence must not
create a task. Supplied intent is reviewed as input metadata, not treated as a
substitute for reading the message.

## Retrieval contract

A relevant ID must contain information that directly answers or operationally
supports the query. Lexical overlap alone is insufficient. All 25 documents in
the isolated corpus are considered, including possible multi-relevance.

## Stateful contract

Only assertion design is reviewed. An acceptable executable case must identify
concrete pre-state, actor/tenant identity, action parameters, fault schedule,
observable post-state, and typed outcome where those facts affect the oracle.
Actual isolation and side-effect properties require execution and cannot be
certified by this review.

The current symbolic `fixture:<name>:v1|v2` references have no fixture
definitions or executor in the repository. Consequently, a conceptually sound
assertion can be acknowledged in the reason, but the case still requires a
concrete fixture contract before it can serve as executable gold.

## Repository evidence used

- `core/intent_recognizer.py`: closed intent enum, templates, specific/generic
  intent hierarchy.
- `agents/orchestration_contracts.py`: Owner and task-plan algebra.
- `agents/agent_orchestrator.py`: documented Owner responsibilities only; no
  Planner results were executed.
- `data/eval/dialogpilot-500-v1/corpus.jsonl`: complete isolated RAG corpus.
- `docs/architecture.md`, `memory/conversation_memory.py`,
  `memory/hybrid_retrieval.py`, `core/chroma_client.py`, `mcp/tool_manager.py`,
  `agents/react_engine.py`, and `services/ticket_service.py`: state and safety
  ownership/transition contracts.

