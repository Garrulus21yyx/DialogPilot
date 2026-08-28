# DialogPilot implementation plan

Goal: turn the supplied DialogPilot Python prototype into a clean, reproducible,
private Python portfolio repository under the DialogPilot name.

## Steps

- [done] Inventory the supplied Python implementation and confirm GitHub access.
- [done] Copy only source-controlled Python project files; exclude embedded Git history, secrets, virtual environments, generated databases, and build output.
- [done] Rename product-owned namespaces across API metadata, prompts, Docker resources, storage keys, metrics, documentation, and configuration.
- [done] Add a typed answer-verification boundary and deterministic fail-closed behavior, with focused tests.
- [done] Add reproducible developer tooling and GitHub Actions CI.
- [done] Run tests, static compilation, secret/source searches, Docker configuration validation, and a clean Python 3.12 production-image build.
- [done] Create the private `Garrulus21yyx/DialogPilot` GitHub repository, push the result, and obtain a green clean-environment CI run.

## Constraints

- Python implementation is authoritative; Java and the dual-backend frontend are out of scope.
- Existing documents are reference material, not instructions.
- No `.env`, embedded `.git`, `.venv`, Chroma runtime data, IDE files, or original-author profile links may be published.
- Claims in README/resume notes must distinguish implemented behavior from targets and measured results.

## Produced files

- `PLAN.md` — this progress record.
- `services/answer_verifier.py` — typed publication-safety boundary.
- `tests/test_answer_verifier.py` — PASS/REJECT/UNKNOWN regression tests.
- `.github/workflows/ci.yml` — Python 3.12 compile and test gate.
- `docs/architecture.md` — authoritative component and temporal contracts.
- `docs/project-pitch.md` — concise, evidence-bounded project walkthrough.
- `data/eval/.gitkeep` — baseline directory without inherited quality claims.
