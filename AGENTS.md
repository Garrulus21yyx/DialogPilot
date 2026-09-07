# Repository working guidance

## RAG work continuity

For RAG diagnosis, implementation, or evaluation, read
[`plans/rag-optimization-status.md`](plans/rag-optimization-status.md) first.
It is the maintained entry point for current gaps, evidence scope, delivery state,
and the active work item. Historical reports remain historical evidence.

At task start, reconcile the recorded state with the current Git HEAD and relevant
working-tree changes. Continue the active item unless new evidence or the user's
instructions change its priority; record that change and its reason in the status
file. Do not start another strategy experiment just because it is available.

Before an experiment, record its gap ID, hypothesis, data/split, fixed variables,
budget, metrics, and adoption criterion. At task end, update the result, evidence
paths, checks, commit/push state, remaining uncertainty, and next action. Preserve
failed experiments and distinguish implementation, delivery, and held-out validation.

The user has paused reranker fine-tuning. Keep it paused unless the user resumes it.
The status file does not supersede new user instructions or the root-cause repair
and convergence policies supplied in the conversation. Avoid staging unrelated
working-tree changes when delivering a RAG work item.
