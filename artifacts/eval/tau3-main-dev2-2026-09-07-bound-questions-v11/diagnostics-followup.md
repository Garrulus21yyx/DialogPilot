# Reply failure observability follow-up — 2026-09-07

Status: diagnostic plumbing verified; customer-dialogue root-cause work remains open.

## Observed gap and repair

The original task 0 stopped before semantic verification; its composer generation was not recorded.
The former `ASSEMBLY_INVALID`, `COMPOSER_FALLBACK` and `ANSWER_SAFE_FALLBACK` labels
discarded the underlying distinction. The original failed candidate cannot be recovered from those labels.

The production Conversation Agent planning/composition provider and verifier now receive the
existing Langfuse SDK callback. The benchmark-injected verifier uses the same callback.
Assembly records its stage, cause chain and retryability using existing StageObservation records.
Schema failures retain validator and paths, not the rejected raw instance; HTTP errors retain status,
not request/response bodies. SDK generation input/output remains in the existing masked trace.

Diagnostics pass through question failure, Completed results, publication execution_stages,
LangGraph serialization and durable run terminal round-trips. Public error text remains safe;
internal errors are not exposed as customer-facing prose. Transient attempt failures are traced/logged;
the existing retry ledger retains its error code, not a new parallel diagnostic store.

## Reply-only diagnostic sample

- Original trace: `39af2872d79ff14b5406ac461e09254d`.
- New trace: `f4e43d6849f1cd5c81b5edef7b7168d1`.
- New session: `tau3-v11-reply-forensics`.
- Scope: captured task 0 assembly context with original tool facts restored from the saved trajectory;
  trace-redacted context and omitted execution_feedback mean this is diagnostic sampling, not exact replay.
- Invoked composition only, with no business tools attached, no exchange submission and no official scoring.
- Result: composition returned a candidate; no semantic verification was performed in this sample.
- Remote observations read-back confirmed generation input/output and a parent observation.
- It does **not** identify the original failure or prove the generated candidate safe to publish.

## Verification and remaining work

325 focused tests passed, including real PostgreSQL HTTP/publication persistence, diagnostic
round-trips, masked cause chains, retry classification and native SDK trace hierarchy.
Independent review identified and prompted fixes for lost replay diagnostics, unsafe exception
stringification and an unsupported SDK observation type.

Still open: business pre-/post-write snapshots incorrectly conflicting; generic fallback unable
to express environment facts; clarification versus action-approval ownership. These findings
must not be reported as fixed by adding tracing. No fresh full τ³ score was produced here.
