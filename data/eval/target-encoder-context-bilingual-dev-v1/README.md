# Bilingual contextual encoder development fixture

This is agent-authored synthetic DEVELOPMENT data, not real customer conversations,
not translated official tau3 supervision, and not a fresh external benchmark.
Each split has 198 examples (66 each zh/en/mixed). `heldout` is the trainer's file
name, not a claim that template-composed examples establish production quality.

Same short reply is paired with READ-refund and WRITE/ambiguous histories. WRITE,
negation and compound actions map to DEFER. General/product examples preserve the
existing frozen capability labels. These fixtures check whether a learned input
can consume bilingual role-aware context; they do not calibrate arbitrary tools.

Group IDs separate the authored prompt families between splits. Answer variants
within each split are correlated, and some vocabulary/templates are shared across
splits. Counts must not be presented as independent real-world observations.
The WRITE histories intentionally mention both an action and a lookup: they are
ambiguous confirmations and cannot authorize a READ shortcut on this supervision.

Do not make an artifact trained only on these fixtures the production default.
Use independent, reviewed multi-turn conversations and report language-specific
precision/coverage, including semantic parameter binding, before promotion.

Reproduce (new output directory required):

```bash
PYTHONPATH=. .venv/bin/python scripts/train_target_encoder.py --contextual \
  --languages zh en mixed --data data/eval/target-encoder-context-bilingual-dev-v1 \
  --output artifacts/target-encoder-context-bilingual-dev-v1
```
