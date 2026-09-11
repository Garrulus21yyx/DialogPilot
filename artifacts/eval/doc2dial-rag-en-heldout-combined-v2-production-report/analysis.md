# Fresh Doc2Dial production-aligned retrieval analysis

This is a report-only projection over two mutually disjoint heldout cohorts.
No production weights, chunk settings, query strategy, or thresholds were
selected from these results.

## Validity

- 156/156 retrieval outcomes were `OK`.
- 468/468 rewrite, expansion, and rerank model calls completed without error.
- Rerank fallback count: 0.
- Core and supplement used the same corpus, retrieval policy, embedding profile,
  generation identity, model policy, and executed-stage contract.
- The prior HTTP 402 core attempt is excluded from this projection.

## Stage results

| Boundary | Complete evidence | Rate | Loss from prior boundary |
|---|---:|---:|---:|
| Candidate Top-20 | 125/156 | 80.1% | 31 misses |
| Reranked Top-5 / prepack | 102/156 | 65.4% | 23 losses |
| Packed Top-5 | 102/156 | 65.4% | 0 losses |

Candidate document recall was 131/156 (84.0%). Thus 25 cases missed the gold
document and another 6 reached the gold document without a child that fully
contained the gold evidence span.

## Reranker counterfactual

Using the fused Candidate order's first five as the no-rerank baseline gives
101/156 complete-evidence hits. Listwise reranking gives 102/156: it rescued 13
cases and harmed 12, for a net gain of one. It improved DMV and StudentAid but
was net harmful on SSA and VA. Running successfully is therefore not evidence
that the present reranker is strong enough.

## Chunk containment

The fixed 512-token / 64-token-overlap projection contained all 270 gold spans
from both cohorts, with zero boundary fragmentation. The chunk construction
does not impose the observed 80.1% Candidate ceiling. Retrieval/ranking failed
to surface the right child. Parent/window expansion was not executed in this
run; it may help cases that retrieve the right document but the wrong child,
but that requires a separate frozen experiment.

## Weak slices

- SSA: Candidate 28/39 (71.8%), packed 21/39 (53.8%).
- Long documents: Candidate 30/39 (76.9%), packed 23/39 (59.0%).
- StudentAid was strongest: Candidate 33/39 (84.6%), packed 29/39 (74.4%).

## Interpretation

The dominant unresolved issue remains first-stage child retrieval (31 cases),
followed by weak Top-20-to-Top-5 reranking (23 cases). Packing is not a current
loss source. The online services are operational, but the reranker's net quality
gain is negligible. The offline chunk boundary contract is sound on these gold
spans, while parent/window expansion is still missing from the evaluated path.
