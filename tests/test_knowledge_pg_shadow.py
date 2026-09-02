"""M2-T05 frozen dark-shadow report contracts."""
import json
from pathlib import Path

import pytest

from application.hybrid_retrieval import (
    HybridRetrievalResult,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalStatus,
)
from evaluation.knowledge_pg_shadow import (
    FrozenKnowledgeShadow,
    KnowledgeShadowContractError,
    run_shadow,
    write_immutable_report,
)


SPEC = Path("evaluation/fixtures/knowledge-pg-shadow-v1/spec.json")


class _Backend:
    def __init__(self, reverse=False):
        self.reverse = reverse

    def retrieve(self, request):
        source_ids = (
            "source-eval-refund-channel", "source-eval-address-change",
            "source-eval-account-profile", "source-eval-client-crash",
            "source-eval-service-500",
        )
        if self.reverse:
            source_ids = tuple(reversed(source_ids))
        candidates = tuple(RetrievalCandidate(
            candidate_id=f"m2-t05-knowledge-pg-pre-exit-v1:{source_id}:0",
            corpus=RetrievalCorpus.KNOWLEDGE,
            generation_id=request.generation_id, source_id=source_id,
            source_revision=f"revision-{index}", rank=index,
            score=1 / index, provenance_sha256="a" * 64,
        ) for index, source_id in enumerate(source_ids, 1))
        return HybridRetrievalResult(
            RetrievalStatus.OK, request.backend_fingerprint,
            request.generation_id, candidates, candidates,
        )


def test_frozen_spec_binds_query_corpus_policy_and_legacy_publisher():
    spec = FrozenKnowledgeShadow.load(SPEC)
    assert spec.raw["publisher"] == "LEGACY_BM25_V1"
    assert spec.raw["active_switch"] is False
    assert spec.raw["policy"]["rrf_k"] == 10
    assert spec.raw["policy"]["candidate_k"] == 20
    assert spec.raw["policy"]["final_k"] == 5
    assert spec.raw["policy"]["context_max_tokens"] == 2600


def test_shadow_report_is_deterministic_and_never_authorizes_canary(tmp_path):
    spec = FrozenKnowledgeShadow.load(SPEC)
    first = run_shadow(
        spec, legacy_backend=_Backend(), postgres_backend=_Backend(reverse=True),
        embed_query=lambda _query: (0.0, 1.0),
    )
    second = run_shadow(
        spec, legacy_backend=_Backend(), postgres_backend=_Backend(reverse=True),
        embed_query=lambda _query: (0.0, 1.0),
    )
    assert first == second
    assert first["case_count"] == 7
    assert first["metrics"]["status_agreement_rate"] == 1.0
    assert first["metrics"]["candidate_order_exact_rate"] == 0.0
    assert first["publisher"] == "LEGACY_BM25_V1"
    assert first["decision"]["release_authorized"] is False

    output = tmp_path / "report.json"
    write_immutable_report(output, first)
    write_immutable_report(output, first)
    with pytest.raises(KnowledgeShadowContractError, match="already differs"):
        write_immutable_report(output, {**first, "case_count": 8})


def test_frozen_spec_rejects_query_checksum_drift(tmp_path):
    spec = FrozenKnowledgeShadow.load(SPEC)
    query = tmp_path / "queries.jsonl"
    query.write_bytes(spec.query_path.read_bytes() + b"\n")
    raw = dict(spec.raw)
    raw["queries"] = {**raw["queries"], "path": "queries.jsonl"}
    raw["corpus"] = {**raw["corpus"], "path": str(spec.corpus_path)}
    copied = tmp_path / "spec.json"
    copied.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(KnowledgeShadowContractError, match="quer"):
        FrozenKnowledgeShadow.load(copied)
