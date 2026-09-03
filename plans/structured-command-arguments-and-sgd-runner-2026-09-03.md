# Structured Command arguments and SGD runner plan

Status: done — implementation complete; held-out Test intentionally unconsumed

## Observed failure and root cause

- Symptom: the frozen SGD benchmark can score arguments, but the production
  Structured LLM path cannot emit them.
- Trigger: any Registry action with `required_arguments`.
- Immediate mechanism: the provider JSON contract has exactly four command
  fields and `StructuredLLMCommandProducer._command()` constructs proposals
  with no `CommandArgument` values.
- Root cause: semantic argument extraction has no general owner.  The later
  `ExplicitIdentifierArgumentBinder` only understands `order_id`, while
  `RoutePolicy` correctly rejects missing Registry-required arguments.

## Positive target contract

- Structured provider commands carry an explicit `arguments` object.
- The producer canonicalizes those values into immutable `CommandArgument`
  objects; blank/duplicate/unsupported argument names fail closed.
- Registry owns the allowed and required argument names.
- The production explicit-ID binder independently verifies `order_id` against
  the user message and never trusts a model-supplied identifier.
- Existing no-argument commands explicitly emit `{}`.
- A benchmark runner materializes SGD history/state, calls the same structured
  producer and RoutePolicy, writes resumable predictions, and never reads Gold
  arguments while predicting.
- Dev can be run repeatedly; Test requires an explicit frozen-run flag.

## Steps

1. **done** — Migrate prompt, parser, binder, fixtures, and consumers to
   the explicit arguments contract.
2. **done** — Build a runtime FlowActionRegistry and TurnState materializer
   from the frozen SGD artifact.
3. **done** — Implement async bounded-concurrency prediction runner with
   provider configuration, resume behavior, and Dev/Test guard.
4. **done** — Add contract, adversarial, runner, and scorer integration tests.
5. **done** — Repair continuation inheritance and preserve official SGD slot
   semantics (descriptions, enum values, canonical date/time context).
6. **done** — Run regression tests, three fixed-sample real-model diagnostics,
   and the complete 1,200-case Dev split.
7. **done** — Publish the evidence-backed Dev report while retaining the Test
   split as an unconsumed held-out gate.

## Files produced or modified

- `plans/structured-command-arguments-and-sgd-runner-2026-09-03.md` — this plan.
- `application/structured_command_prompt.py` — provider argument contract.
- `application/structured_command_producer.py` — typed argument parsing.
- `application/route_policy_v2.py` — Registry-owned required/optional/unknown
  argument validation.
- `application/command_argument_binding.py` — explicit order ID cross-check.
- `evaluation/public_sgd/runtime.py` — Registry/state/history materialization and
  predicted state transition.
- `evaluation/public_sgd/runner.py` — bounded-concurrency resumable runner.
- `scripts/run_sgd_structured_command_eval.py` — provider-backed CLI and heldout
  guard.
- `tests/test_public_sgd_runner.py` and migrated command fixtures — E2E and
  adversarial coverage.
- `docs/sgd-command-benchmark.zh-CN.md` — actual run instructions and status.

## Verification evidence

- Structured/public command regression: `39 passed, 2 skipped`.
- Full repository run: `884 passed, 156 skipped`; three failures are outside
  this change surface (one existing evolution bundle/retrieval-policy mismatch
  and two stateful fixtures requiring `TEST_DATABASE_URL`/`DATABASE_URL`).
- Fake-provider E2E traversed the production parser and RoutePolicy and scored
  `1.0` across four mapping rules.
- The v2 official full pool regenerated 15,415 Dev and 31,620 Test cases; the
  frozen balanced artifact validates at 2,400 cases.
- Real `deepseek-v4-flash` full Dev: Status 67.50%, Command 57.83%, Flow 56.67%,
  Arguments 42.33%, Next state 67.92%, E2E Exact 57.50% (690/1,200).
- All 1,200 predictions are present; prediction SHA-256 is
  `16895425d1b909cdf976cf8f79608bbf88c26a6d867851046be08e12daaf8c60`.
- Held-out Test was not run.
