# RAG PydanticAI Structured Output Migration

## Goal

Replace the grounded RAG generator's free-text JSON extraction and handwritten
repair retry with a local PydanticAI structured-output boundary, while keeping
DialogPilot's existing orchestration, evidence, authorization, and domain result
contracts authoritative.

## Constraints

- Do not migrate `AgentOrchestrator`, `TaskGraph`, ReAct, or tool execution.
- Preserve fail-closed citation/evidence business validation.
- Keep provider/model selection owned by `ModelPolicy`.
- Use at most one output-correction retry and expose typed terminal failure.
- Verify the existing 36-case long-document contract after unit/integration tests.

## Steps

1. **done** — Inspected the generator, provider adapter, tests, dependency constraints, and live DeepSeek compatibility.
2. **done** — Defined a single-authority segment/evidence output with dynamic business validation.
3. **done** — Implemented the local PydanticAI boundary and migrated affected tests/docs.
4. **done** — Passed 364 tests plus live DeepSeek generator and reranker smoke calls.
5. **done** — Re-ran 36 cases across three topologies: 0/108 contract errors, 6/36 typed abstentions per topology; topology candidates remain rejected.

## Files

- `plans/rag-pydanticai-structured-output-2026-09-01.md` — this plan.
- `requirements.txt` — current PydanticAI/Anthropic/Pydantic compatibility pins.
- `mcp/grounded_answer_generator.py` — single-authority segment/evidence output and typed retry boundary.
- `core/model_policy.py` — Anthropic SDK 1.x sampling-parameter transport projection.
- `core/llm_metrics.py` — PydanticAI aggregate usage projection into existing observability.
- `artifacts/eval/doc2dial-rag-long8000-dev-v1/context-topology-pydanticai-v5-full-report.json` — frozen Dev replay.

## Verification record

- `PYTHONPATH=. .venv/bin/pytest -q` → `364 passed, 2 skipped`.
- Live `deepseek-v4-pro` tool-output smoke → answered with one valid citation, one request.
- Live `deepseek-v4-flash` rerank smoke after SDK 1.x transport migration → exact permutation, no fallback.
- Dev replay → 108/108 first-attempt structured outputs; generation requests `203→108`, output tokens `78,014→27,248`; cached-inclusive input tokens `~472k→~530k`.
- Baseline pipeline P95 `18.29s→10.22s`; no parent-child candidate passed multi-condition/harmful-context gates.
