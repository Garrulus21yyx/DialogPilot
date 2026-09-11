# LLM review plan: dialogpilot-500-v1

## Goal

Independently review all 500 cases in `data/eval/dialogpilot-500-v1` and produce
an auditable per-case Reviewer A artifact. The review may propose corrections,
but it must not promote any case to `human_reviewed` or derive routing truth
from the current Planner output.

## Constraints

- Use repository-owned label, routing, retrieval, memory, and tool contracts.
- Review each case as `accept`, `revise`, or `ambiguous` with confidence,
  reason, and evidence.
- Treat self-reported confidence as triage metadata, not calibrated probability.
- For stateful cases, review assertion design only; do not claim runtime safety.
- Preserve the current dataset and manifest unchanged.
- Record that this review is not fully blind: before the review began, the
  reviewer saw the dataset overview, the reported 120/120 Planner agreement,
  and a small prefix of expected intent labels, but no prior Reviewer A/B file.

## Steps

| Step | Status | Deliverables / verification |
|---|---|---|
| 1. Identify authoritative contracts and construct blinded review packets | done | `artifacts/eval/reviews/dialogpilot-500-v1-reviewer-codex-a.contract.md`; expected hidden during first-pass packet extraction where possible |
| 2. Review all 180 intent/OOS cases | done | 157 accept, 9 revise, 14 ambiguous |
| 3. Review all 120 routing cases | done | 115 accept, 5 revise; Planner not executed |
| 4. Review all 100 retrieval cases against the isolated corpus | done | 100 accept with corpus evidence |
| 5. Review all 100 stateful assertion designs | done | 100 revise pending concrete fixtures/runtime execution; 50 semantic groups |
| 6. Validate artifact completeness/schema and summarize disputes | done | 500 unique IDs; 372 accept, 114 revise, 14 ambiguous; 76 human-queue groups; 16 dataset tests passed |

## Produced files

- This plan file.
- `artifacts/eval/reviews/dialogpilot-500-v1-reviewer-codex-a.contract.md`.
- `artifacts/eval/reviews/render_dialogpilot_500_reviewer_codex_a.py`.
- `artifacts/eval/reviews/dialogpilot-500-v1-reviewer-codex-a.jsonl`.
- `artifacts/eval/reviews/dialogpilot-500-v1-reviewer-codex-a.summary.json`.
- `artifacts/eval/reviews/dialogpilot-500-v1-reviewer-codex-a.human-queue.jsonl`.

## Verification record

- Review JSONL: 500 lines, 500 unique case IDs, exact equality with dataset IDs.
- Required judgment fields, confidence range, non-empty reasons/evidence, and
  empty correction objects on accepted cases: passed.
- Human queue: 128 cases collapsed to 76 semantic groups, including 50
  stateful fixture groups.
- Source dataset checksum remained
  `00c32e28eda8b9026482a4135561fba5287747982dd50c15127db126fe94b8ac`.
- `.venv/bin/python -m pytest tests/test_layered_eval_dataset.py -q`: 16 passed.
- `.venv/bin/python -m evaluation.dataset data/eval/dialogpilot-500-v1`: passed.
- Note: direct `.venv/bin/pytest` collection failed because the console entry
  point did not place the repository on the import path; rerunning through the
  same interpreter with `python -m pytest` passed.
