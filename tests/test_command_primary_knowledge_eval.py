from __future__ import annotations

import asyncio
import json

from application.hybrid_retrieval import RetrievalStatus
from application.knowledge_retriever import (
    KnowledgeCandidateResult,
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
    KnowledgeRetriever,
)
from evaluation.command_primary_eval.contracts import EvalCase, EvaluationStatus
from evaluation.command_primary_eval.knowledge import (
    KnowledgeConsumption,
    KnowledgeDirectAdapter,
)
from evaluation.command_primary_eval.knowledge_runner import KnowledgeDirectRunner


SHA = "a" * 64


def _request(case: EvalCase) -> KnowledgeRetrievalRequest:
    policy = KnowledgeRetrievalPolicy(
        policy_version="knowledge-eval-v1",
        backend_fingerprint="fixture-hybrid-v1",
        lexical_provider="fixture-fts-v1",
        transformer_version="fixture-rewrite-v1",
        embedding_version="fixture-dense-v1",
        reranker_version="fixture-rerank-v1",
        packer_version="context-packer-v1",
    )
    return KnowledgeRetrievalRequest(
        tenant_id="tenant-one",
        user_scope="user-one",
        authorization_fingerprint="auth-v1",
        acl_policy_fingerprint="acl-v1",
        deletion_epoch=0,
        requirement_signature="knowledge.active_source",
        query=case.message,
        history=(),
        conversation_range_hash="range-v1",
        locale="zh-CN",
        product=None,
        manifest_fingerprint=SHA,
        generation_id="generation-one",
        policy=policy,
    )


class _Transformer:
    async def standalone(self, query, history):
        assert history == ()
        return query, None


class _Source:
    def __init__(self) -> None:
        self.calls = 0

    async def search_variants_async(self, request, variants, *, top_k):
        self.calls += 1
        assert request.query == "退款通常多久到账？"
        assert variants == [("raw", request.query, 1.0)]
        assert top_k == 20
        return KnowledgeCandidateResult(RetrievalStatus.OK, ({
            "chunk_id": "refund-policy::chunk-0",
            "source_id": "refund-policy",
            "source_revision": "revision-one",
            "source_checksum": SHA,
            "source_start_char": 0,
            "source_end_char": 12,
            "content": "退款审核后通常原路退回。",
            "title": "退款政策",
            "source_type": "text",
            "scope": "public",
            "scope_decision": "allowed_public",
            "index_manifest_fingerprint": SHA,
            "ranks": {"raw:lexical": 1, "raw:dense": 1},
            "score": 1.0,
        },))


class _Reranker:
    async def rerank(self, query, candidates):
        assert query == "退款通常多久到账？"
        return tuple(item["chunk_id"] for item in candidates), False


class _GroundedConsumer:
    def __init__(self) -> None:
        self.seen_ids = ()

    async def consume(self, case, evidence):
        self.seen_ids = tuple(item.chunk_id for item in evidence.items)
        assert case.case_id == "knowledge-refund-policy-001"
        return KnowledgeConsumption(
            evidence_ids=self.seen_ids,
            outcome={"answer_status": "GROUNDED", "task_succeeded": True},
            token_usage=19,
        )


def test_knowledge_direct_runner_measures_pack_consumption_and_outcome(
    tmp_path,
) -> None:
    case = EvalCase(
        case_id="knowledge-refund-policy-001",
        message="退款通常多久到账？",
        initial_state={},
        expected={
            "invocation": "REQUIRED",
            "retrieval_status": "OK",
            "evidence_ids": ["refund-policy::chunk-0"],
            "evidence_spans": [{
                "document_id": "refund-policy",
                "start_char": 0,
                "end_char": 12,
            }],
            "min_evidence_recall": 1.0,
            "consumed_evidence_ids": ["refund-policy::chunk-0"],
            "outcome": {
                "answer_status": "GROUNDED",
                "task_succeeded": True,
            },
        },
        slice="knowledge-policy",
    )
    source = _Source()
    consumer = _GroundedConsumer()
    adapter = KnowledgeDirectAdapter(
        KnowledgeRetriever(
            candidate_source=source,
            transformer=_Transformer(),
            reranker=_Reranker(),
        ),
        _request,
        consumer,
    )

    report = asyncio.run(KnowledgeDirectRunner(adapter).run(
        (case,),
        tmp_path,
        run_id="knowledge-run-001",
        dataset_id="knowledge-direct-fixture-v1",
        split="dev",
        configuration={"index_generation": "generation-one"},
    ))

    assert source.calls == 1
    assert consumer.seen_ids == ("refund-policy::chunk-0",)
    assert report.status is EvaluationStatus.PASS
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    ]

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    prediction = json.loads((tmp_path / "predictions.jsonl").read_text())
    persisted_report = json.loads((tmp_path / "report.json").read_text())

    assert manifest["component"] == "knowledge"
    assert manifest["adapter_version"] == "knowledge-direct-adapter-v1"
    assert prediction["trigger"]["passed"] is True
    assert prediction["artifact"]["detail"]["evidence_ids"] == [
        "refund-policy::chunk-0",
    ]
    assert prediction["artifact"]["detail"]["evidence_refs"][0] == {
        "checksum": SHA,
        "end_char": 12,
        "scope": "public",
        "source_id": "refund-policy",
        "source_revision": "revision-one",
        "source_type": "text",
        "start_char": 0,
    }
    assert prediction["artifact"]["detail"]["metrics"] == {
        "document_recall": 1.0,
        "evidence_recall": 1.0,
        "mrr": 1.0,
        "ndcg": 1.0,
    }
    assert prediction["consumption"]["detail"]["actual_evidence_ids"] == [
        "refund-policy::chunk-0",
    ]
    assert prediction["outcome"]["detail"]["actual"] == {
        "answer_status": "GROUNDED",
        "task_succeeded": True,
    }
    assert prediction["cost"]["token_usage"] == 19
    assert persisted_report["dimensions"]["retrieval"] == {
        "document_recall": 1.0,
        "evidence_recall": 1.0,
        "mrr": 1.0,
        "ndcg": 1.0,
    }
