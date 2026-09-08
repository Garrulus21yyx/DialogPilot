# Semantic contextual development data v2

Synthetic extension of ../encoder-language-v3. Retains the Bitext-derived data's
Copyright (c) Bitext Innovations 2024 and CDLA-Sharing-1.0 license; see
[source, revision and attribution](../encoder-language-v3/README.md).
The external subset and modifications remain under https://cdla.dev/sharing-1-0/.

Built with scripts/build_semantic_encoder_data.py, ../semantic-context-families-v1.json
and ../semantic-boundary-operators-v1.json. Adds complete-utterance compound goals,
read-only restrictions and business-state negatives, preserving each source family split.
Base data partitions were already consumed. Variants are not independent examples;
exact-normalized grouping cannot rule out paraphrase overlap in the upstream Bitext data.
Only one of the 24 new context families landed in the development heldout partition.

The final independent160 dialogue challenge is separate, exact-disjoint from these
splits, and never trained back in. The candidate failed adoption; see
docs/semantic-encoder-2026-09-08.zh-CN.md. No runtime rule or tool permission derives
from a phrase in this file. Model scripts read labels only for offline training/gating.
