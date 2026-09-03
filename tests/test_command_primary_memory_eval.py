from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from application.hybrid_retrieval import (
    EpisodeSearchScope,
    HybridRetrievalRequest,
    HybridRetrievalResult,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalStatus,
)
from application.memory_retrieval_policy import DEFAULT_MEMORY_RETRIEVAL_POLICY
from application.service_episode_retriever import (
    ServiceEpisodeFreshnessMode,
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetrievalPurpose,
    ServiceEpisodeRetriever,
)
from evaluation.command_primary_eval.contracts import EvalCase, EvaluationStatus
from evaluation.command_primary_eval.memory import (
    MemoryDirectAdapter,
)
from evaluation.command_primary_eval.memory_runner import MemoryDirectRunner


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


class Backend:
    def __init__(self, candidate: RetrievalCandidate) -> None:
        self.candidate = candidate
        self.calls = 0

    def retrieve(self, request: HybridRetrievalRequest) -> HybridRetrievalResult:
        self.calls += 1
        return HybridRetrievalResult(
            status=RetrievalStatus.OK,
            backend_fingerprint=request.backend_fingerprint,
            generation_id=request.generation_id,
            lexical_candidates=(self.candidate,),
            index_watermark="episode-outbox:42",
        )


def _policy(
    purpose: ServiceEpisodeRetrievalPurpose,
) -> ServiceEpisodeRetrievalPolicy:
    return ServiceEpisodeRetrievalPolicy(
        version=f"{purpose.value.lower()}-eval-v1",
        fusion=DEFAULT_MEMORY_RETRIEVAL_POLICY,
        minimum_fused_relevance=0.0,
        freshness_max_age_seconds=(
            90 * 86400
            if purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
            else None
        ),
        purpose=purpose,
        freshness_mode=(
            ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT
            if purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
            else ServiceEpisodeFreshnessMode.RANK_ONLY
        ),
        unique_binding_margin=(
            0.001
            if purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
            else None
        ),
    )


def _candidate(
    purpose: ServiceEpisodeRetrievalPurpose,
    episode_id: str,
    provenance: str,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        candidate_id=f"candidate-{episode_id}",
        corpus=RetrievalCorpus.SERVICE_EPISODE,
        generation_id="generation-1",
        source_id=episode_id,
        source_revision="1",
        rank=1,
        score=1.0,
        provenance_sha256=provenance,
        freshness_at=NOW.isoformat(),
    )


def test_memory_direct_runner_records_both_explicit_purposes(tmp_path) -> None:
    fixtures = {
        ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION: (
            "episode-refund-previous",
            "a" * 64,
            "UNIQUE_BINDING",
        ),
        ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE: (
            "episode-e401-solution",
            "b" * 64,
            "EVIDENCE_AVAILABLE",
        ),
    }
    backends = {
        purpose: Backend(_candidate(purpose, episode_id, provenance))
        for purpose, (episode_id, provenance, _outcome) in fixtures.items()
    }
    retrievers = {
        purpose: ServiceEpisodeRetriever(backends[purpose], _policy(purpose))
        for purpose in fixtures
    }

    def request_builder(case, policy):
        memory_input = case.initial_state["memory_request"]
        return HybridRetrievalRequest(
            tenant_id="tenant-1",
            corpus=RetrievalCorpus.SERVICE_EPISODE,
            backend_fingerprint="backend-1",
            generation_id="generation-1",
            policy_fingerprint=policy.fingerprint,
            query_text=case.message,
            query_embedding=None,
            scope=EpisodeSearchScope(
                user_id="user-1",
                entity_ids=tuple(memory_input.get("entity_ids", ())),
            ),
        )

    cases = tuple(
        EvalCase(
            case_id=f"memory-{purpose.value.lower()}",
            message=(
                "之前那个退款"
                if purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
                else "上次 E401 是怎么解决的"
            ),
            initial_state={
                "memory_request": {
                    "purpose": purpose.value,
                    "entity_ids": (
                        ["DP1234"]
                        if purpose
                        is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
                        else []
                    ),
                    "now": NOW.isoformat(),
                    "top_k": 2 if purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION else 5,
                },
            },
            expected={
                "invocation": "REQUIRED",
                "episode_ids": [episode_id],
                "evidence_refs": [provenance],
                "consumed_evidence_refs": [provenance],
                "retrieval_status": "OK",
                "purpose_outcome": purpose_outcome,
            },
            slice=purpose.value.lower(),
        )
        for purpose, (episode_id, provenance, purpose_outcome) in fixtures.items()
    )
    adapter = MemoryDirectAdapter(
        retrievers=retrievers,
        request_builder=request_builder,
        consumer=lambda _case, result: tuple(
            hit.provenance_sha256 for hit in result.hits
        ),
        version="service-episode-memory-direct-v1",
    )

    report = asyncio.run(MemoryDirectRunner(adapter).run(
        cases,
        tmp_path,
        run_id="memory-direct-run-1",
        dataset_id="memory-direct-positive-v1",
        split="dev",
        configuration={"memory_scope": "service_episode_only"},
    ))

    assert report.status is EvaluationStatus.PASS
    assert report.passed == 2
    assert all(backend.calls == 1 for backend in backends.values())
    assert {path.name for path in tmp_path.iterdir()} == {
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    }

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    predictions = [
        json.loads(line)
        for line in (tmp_path / "predictions.jsonl").read_text().splitlines()
    ]
    persisted_report = json.loads((tmp_path / "report.json").read_text())

    assert manifest["component"] == "memory_rag"
    assert manifest["configuration"] == {
        "memory_scope": "service_episode_only",
    }
    assert {item["trigger"]["detail"]["purpose"] for item in predictions} == {
        "REFERENCE_RESOLUTION",
        "HISTORICAL_EVIDENCE",
    }
    assert all(item["artifact"]["passed"] for item in predictions)
    assert all(
        item["artifact"]["detail"]["recall_at_k"] == 1.0
        for item in predictions
    )
    assert all(
        item["artifact"]["detail"]["mrr"] == 1.0
        for item in predictions
    )
    assert all(item["consumption"]["passed"] for item in predictions)
    assert all(item["outcome"]["passed"] for item in predictions)
    assert persisted_report["dimensions"]["retrieval_quality"] == {
        "evaluated_cases": 2,
        "mean_mrr": 1.0,
        "mean_recall_at_k": 1.0,
    }
    assert persisted_report["dimensions"]["cost"]["total_invocations"] == 2
