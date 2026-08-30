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

Do not tune prompts on `heldout`. Variants sharing one semantic scenario must
use the same `group_id`, and the validator rejects a group that crosses the
dev/held-out boundary.
