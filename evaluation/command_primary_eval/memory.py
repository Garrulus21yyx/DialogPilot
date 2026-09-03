"""Direct ServiceEpisode Memory RAG evaluation with shared run artifacts."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from time import perf_counter

from application.hybrid_retrieval import HybridRetrievalRequest
from application.service_episode_retriever import (
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetrievalPurpose,
    ServiceEpisodeRetrievalResult,
    ServiceEpisodeRetriever,
)
from evaluation.command_primary_eval.contracts import (
    CheckResult,
    CostResult,
    DirectEvaluationResult,
    EvalCase,
)


MemoryRequestBuilder = Callable[
    [EvalCase, ServiceEpisodeRetrievalPolicy], HybridRetrievalRequest
]
MemoryConsumer = Callable[
    [EvalCase, ServiceEpisodeRetrievalResult], Sequence[str]
]


class MemoryDirectAdapter:
    """Adapt explicit ServiceEpisode requests; current-turn state is out of scope."""

    def __init__(
        self,
        *,
        retrievers: Mapping[ServiceEpisodeRetrievalPurpose, ServiceEpisodeRetriever],
        request_builder: MemoryRequestBuilder,
        consumer: MemoryConsumer,
        version: str,
    ) -> None:
        self._retrievers = dict(retrievers)
        self._request_builder = request_builder
        self._consumer = consumer
        self.version = version

    async def evaluate(self, case: EvalCase) -> DirectEvaluationResult:
        memory_input = case.initial_state["memory_request"]
        purpose = ServiceEpisodeRetrievalPurpose(str(memory_input["purpose"]))
        retriever = self._retrievers[purpose]
        request = self._request_builder(case, retriever.policy)
        now_value = memory_input.get("now")
        now = datetime.fromisoformat(str(now_value)) if now_value else None

        started = perf_counter()
        result = retriever.retrieve(
            request,
            purpose=purpose,
            explicit_time_reference=bool(
                memory_input.get("explicit_time_reference", False)
            ),
            now=now,
            top_k=int(memory_input.get("top_k", 5)),
        )
        latency_ms = (perf_counter() - started) * 1000

        episode_ids = tuple(hit.episode_id for hit in result.hits)
        evidence_refs = tuple(hit.provenance_sha256 for hit in result.hits)
        consumed_refs = tuple(self._consumer(case, result))
        expected_episode_ids = tuple(case.expected.get("episode_ids", ()))
        expected_evidence_refs = tuple(case.expected.get("evidence_refs", ()))
        expected_consumed_refs = tuple(
            case.expected.get("consumed_evidence_refs", ())
        )
        expected_unique = set(expected_episode_ids)
        recalled = len(expected_unique.intersection(episode_ids))
        recall_at_k = (
            recalled / len(expected_unique) if expected_unique else None
        )
        first_relevant_rank = next(
            (
                rank
                for rank, episode_id in enumerate(episode_ids, start=1)
                if episode_id in expected_unique
            ),
            None,
        )
        mrr = (
            1.0 / first_relevant_rank
            if first_relevant_rank is not None
            else 0.0 if expected_unique else None
        )

        return DirectEvaluationResult(
            trigger=CheckResult(
                case.expected.get("invocation", "REQUIRED") == "REQUIRED",
                {
                    "expected": case.expected.get("invocation", "REQUIRED"),
                    "actual": "INVOKED",
                    "purpose": purpose.value,
                },
            ),
            artifact=CheckResult(
                episode_ids == expected_episode_ids
                and evidence_refs == expected_evidence_refs,
                {
                    "episode_ids": list(episode_ids),
                    "evidence_refs": list(evidence_refs),
                    "recall_at_k": recall_at_k,
                    "mrr": mrr,
                    "k": int(memory_input.get("top_k", 5)),
                    "hits": [hit.to_dict() for hit in result.hits],
                },
            ),
            consumption=CheckResult(
                consumed_refs == expected_consumed_refs,
                {"consumed_evidence_refs": list(consumed_refs)},
            ),
            outcome=CheckResult(
                result.status.value == case.expected["retrieval_status"]
                and result.purpose_outcome.value
                == case.expected["purpose_outcome"]
                and result.purpose is purpose,
                {
                    "purpose": result.purpose.value,
                    "retrieval_status": result.status.value,
                    "purpose_outcome": result.purpose_outcome.value,
                    "detail_code": result.detail_code,
                },
            ),
            cost=CostResult(
                latency_ms=latency_ms,
                token_usage=0,
                invocation_count=1,
            ),
        )
