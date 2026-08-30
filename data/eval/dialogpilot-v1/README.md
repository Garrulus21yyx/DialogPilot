# DialogPilot layered evaluation seed v1

This directory contains a small **provisional** repository-specific seed set,
not a production benchmark and not a claimed accuracy result.

- `cases.jsonl`: intent, routing, retrieval, and stateful/security cases.
- `corpus.jsonl`: stable documents referenced by retrieval gold IDs.
- `manifest.json`: version, provenance, counts, and SHA-256 content identity.

Every case starts as `review.status=provisional`. A human reviewer must check the
input, expected behavior, ambiguity, and split before changing it to
`human_reviewed`. Default benchmark scoring excludes provisional and
auto-mapped cases from project-gold metrics.

Validate after any edit:

```bash
python -m evaluation.dataset data/eval/dialogpilot-v1
```

After checking and, if needed, correcting a case input/expected label, promote
it through the review command so required audit metadata and checksums are
written together:

```bash
python scripts/review_eval_dataset.py data/eval/dialogpilot-v1 \
  --case-id intent-dev-negation-01 \
  --reviewer reviewer-a --notes 'intent and ambiguity checked' \
  --confirm-human-review
```

Then produce a complete prediction JSONL for the chosen split and score it with:

```bash
python -m evaluation.benchmark data/eval/dialogpilot-v1 predictions.jsonl --split heldout
```

For a provisional pipeline dry run only, add `--include-non-gold`. That switch
does not promote cases and its result must not be presented as project accuracy.

Do not tune prompts on `heldout`. Variants sharing one semantic scenario must
use the same `group_id`, and the validator rejects a group that crosses the
dev/held-out boundary.
