"""Direct Knowledge retrieval evaluation with explicit downstream consumption."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Mapping, Protocol

from application.knowledge_retriever import (
    EvidencePackResult,
    KnowledgeRetrievalRequest,
    KnowledgeRetriever,
)
from evaluation.command_primary_eval.contracts import (
    CheckResult,
    CostResult,
    DirectEvaluationResult,
    EvalCase,
)
from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from mcp.evidence_pack import EvidencePack


@dataclass(frozen=True)
class KnowledgeConsumption:
    """Observable result from the real consumer of one EvidencePack."""

    evidence_ids: tuple[str, ...]
    outcome: Mapping[str, Any]
    token_usage: int = 0


class EvidencePackConsumer(Protocol):
    async def consume(
        self, case: EvalCase, evidence: EvidencePack,
    ) -> KnowledgeConsumption: ...


class KnowledgeDirectAdapter:
    """Run the Knowledge owner, then observe one explicit pack consumer."""

    version = "knowledge-direct-adapter-v1"

    def __init__(
        self,
        retriever: KnowledgeRetriever,
        request_builder: Callable[[EvalCase], KnowledgeRetrievalRequest],
        consumer: EvidencePackConsumer,
    ) -> None:
        self._retriever = retriever
        self._request_builder = request_builder
        self._consumer = consumer

    async def evaluate(self, case: EvalCase) -> DirectEvaluationResult:
        started = perf_counter()
        result = await self._retriever.retrieve(self._request_builder(case))
        pack = result.evidence_pack
        expected_invocation = str(case.expected.get("invocation") or "REQUIRED")
        trigger = CheckResult(expected_invocation == "REQUIRED", {
            "expected": expected_invocation,
            "actual": "INVOKED",
        })

        artifact = _artifact_check(case, result)
        observed = (
            await self._consumer.consume(case, pack)
            if pack is not None else KnowledgeConsumption((), {})
        )
        available_ids = {item.chunk_id for item in pack.items} if pack else set()
        expected_consumed = tuple(map(
            str, case.expected.get("consumed_evidence_ids") or (),
        ))
        invalid_ids = tuple(
            item for item in observed.evidence_ids if item not in available_ids
        )
        consumption = CheckResult(
            observed.evidence_ids == expected_consumed and not invalid_ids,
            {
                "expected_evidence_ids": expected_consumed,
                "actual_evidence_ids": observed.evidence_ids,
                "invalid_evidence_ids": invalid_ids,
            },
        )
        expected_outcome = dict(case.expected.get("outcome") or {})
        actual_outcome = dict(observed.outcome)
        outcome = CheckResult(actual_outcome == expected_outcome, {
            "expected": expected_outcome,
            "actual": actual_outcome,
        })
        return DirectEvaluationResult(
            trigger=trigger,
            artifact=artifact,
            consumption=consumption,
            outcome=outcome,
            cost=CostResult(
                latency_ms=(perf_counter() - started) * 1000,
                token_usage=observed.token_usage,
                invocation_count=1,
            ),
        )


def _artifact_check(case: EvalCase, result: EvidencePackResult) -> CheckResult:
    pack = result.evidence_pack
    expected_status = str(case.expected.get("retrieval_status") or "OK")
    actual_ids = tuple(item.chunk_id for item in pack.items) if pack else ()
    expected_ids = set(map(str, case.expected.get("evidence_ids") or ()))
    spans = tuple(EvidenceSpan(**dict(item)) for item in (
        case.expected.get("evidence_spans") or ()
    ))
    metrics = _retrieval_metrics(case, pack, spans)
    minimum_recall = float(case.expected.get("min_evidence_recall", 1.0))
    passed = (
        result.status.value == expected_status
        and expected_ids.issubset(actual_ids)
        and metrics["evidence_recall"] >= minimum_recall
    )
    return CheckResult(passed, {
        "expected_status": expected_status,
        "actual_status": result.status.value,
        "evidence_ids": actual_ids,
        "evidence_refs": tuple(
            item.source_ref.to_dict() for item in pack.items
        ) if pack else (),
        "metrics": metrics,
        "detail_code": result.detail_code,
    })


def _retrieval_metrics(
    case: EvalCase,
    pack: EvidencePack | None,
    spans: tuple[EvidenceSpan, ...],
) -> Mapping[str, float]:
    ranked_ids = tuple(item.chunk_id for item in pack.items) if pack else ()
    hits = {
        item.chunk_id: {
            "document_id": item.source_ref.source_id,
            "source_start_char": item.source_ref.start_char,
            "source_end_char": item.source_ref.end_char,
        }
        for item in pack.items
    } if pack else {}
    rag_case = RagCase(
        case_id=case.case_id,
        group_id=case.case_id,
        split="dev",
        query=case.message,
        evidence=spans,
        answerable=bool(spans),
    )
    return evaluate_ranked_hits(
        rag_case, ranked_ids, hits, top_k=max(1, len(ranked_ids)),
    )
