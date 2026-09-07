# Telemetry masking scope

Status: bounded telemetry repair verified. Implementation, real export readback and independent review agree; this does not close the separate τ³ business task.

Cause: whole-string numeric substitution changes IDs and JSON object keys inside encoded messages; substring key matching also removes prompt_tokens. Simulator capture duplicates the recursive traversal and introduces its own exceptions.

Contract: only export copies change; JSON keys, cardinality, business identifiers, prices, timestamps and usage survive. Declared credential/contact fields and reasoning content are masked; disabled-thinking configuration is preserved. Structured and encoded-JSON forms have equivalent policy. Use the existing Langfuse boundary, boltons traversal, scrubadub detectors/replacement and standard JSON parsers; no custom replacement engine, shell-text parser or recursive container engine. Application-owned declarative field/credential patterns remain necessary scope configuration. Unknown free-text personal names/addresses are not claimed detectable without contextual fields.

Steps: inspect SDK -> implement shared policy -> migrate trace/simulator consumers -> structural/nonmutation/security tests -> SDK export roundtrip and report -> commit/push. Historical corrupted traces cannot be repaired in place or claimed exact replays.

Owner corrections found by independent review: structured internal span attributes must be masked before formatting; redaction markers and replay rejection must agree; decoded containers must remain alive during identity-memoized traversal; schema declarations and instance values have different semantics. Validated object schemas propagate declaration ownership through schema-valued keywords, not through instance defaults/examples/extensions. Source-span matching stays in scrubadub, avoiding escaped-token replacement errors.

Non-goals: no business writes or changes to Agent planning, approval or model context; no universal PII compliance claim. Langfuse's attribute hook does not cover span names/events/resources/links or pre-hook media upload. Those surfaces must not contain private payloads. Other exporters need their own configured boundary. This is not a new τ³ business-success score.

Evidence: actual SDK export and official CLI observations readback, trace `00f13dfa7db00dba71112f5187df0e6a`: all 40 variants, prices, timestamp, prompt_tokens, tool schema and truncated prose retained; synthetic contact/credential/reasoning values absent. SDK export-policy failure test confirms no raw batch is exported. Known regressions are supplemented by generated structure and schema-child tests and a fresh-context review.

Final validation: 85 tests passed with a fresh isolated PostgreSQL test database (no skips). The independent reviewer additionally checked 400 generated structure/nesting cases and 120 schema-keyword/instance boundary cases; final review found no blocker in the declared scope. Test suites: telemetry_privacy, langfuse_framework, tau3_user_diagnostics, tool_security_trace, security_threat_model, persistent_tracing. No LLM benchmark or business operation was executed.
