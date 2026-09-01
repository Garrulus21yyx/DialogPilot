"""Replay, drift detection and reporting limits of the M0 behavior baseline."""
import copy

import pytest

from core.intent_recognizer import _VOTE_WEIGHTS
from core.rag_policy import DEFAULT_RAG_RETRIEVAL_POLICY
from evaluation.behavior_baseline import (
    BehaviorBaselineError,
    BehaviorRunRecord,
    DECISION_POLICY_BASELINE_V1,
    build_behavior_baseline,
    load_behavior_baseline,
    verify_replay_environment,
    write_behavior_baseline,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
GIT_A = "1" * 40
GIT_B = "2" * 40


def _record(route="knowledge_qa", latency=10.0):
    return BehaviorRunRecord.from_mapping({
        "request_id": f"request-{route}",
        "trace_id": f"trace-{route}",
        "route": route,
        "latency_ms": latency,
        "model_calls": 2,
        "tool_calls": 1,
        "publication_status": "completed",
        "failure_type": "none",
        "stages": [{"stage": "delivery", "status": "ok", "detail": {}}],
    })


def _manifest():
    return build_behavior_baseline(
        baseline_id="m0-v1",
        created_at="2026-09-02T00:00:00Z",
        commit_sha=GIT_A,
        tree_sha=GIT_B,
        bundle={"version": "agent-v2", "content_hash": SHA_A},
        model_policy={"provider": "test", "roles": {}},
        rag_index_manifest={"manifest_fingerprint": SHA_B},
        datasets=[{
            "dataset_id": "dataset-v1",
            "version": "1.0.0-provisional",
            "classification": "provisional",
            "manifest_path": "data/eval/dataset-v1/manifest.json",
            "manifest_sha256": SHA_A,
            "cases_sha256": SHA_B,
        }],
        records=[
            _record("knowledge_qa", 10.0),
            _record("knowledge_qa", 30.0),
            _record("business_tool", 20.0),
        ],
    )


def test_baseline_is_replayable_and_aggregates_operational_route_facts(tmp_path):
    manifest = _manifest()
    path = tmp_path / "behavior-baseline.json"
    write_behavior_baseline(path, manifest)
    replayed = load_behavior_baseline(path)

    assert replayed == manifest
    assert replayed["production_accuracy_claim"] is False
    assert "accuracy" not in replayed
    assert replayed["route_measurements"]["knowledge_qa"] == {
        "count": 2,
        "latency_ms": {"p50": 10.0, "p95": 30.0},
        "model_calls": 4,
        "tool_calls": 2,
        "publication_statuses": {"completed": 2},
        "failure_types": {"none": 2},
    }
    assert replayed["records"][0]["request_id"]
    assert replayed["records"][0]["trace_id"]


@pytest.mark.parametrize(
    "changed",
    ["commit", "bundle_version", "bundle_hash", "index"],
)
def test_replay_fails_closed_when_a_pinned_version_changes(changed):
    values = {
        "commit_sha": GIT_A,
        "bundle_version": "agent-v2",
        "bundle_sha256": SHA_A,
        "rag_index_manifest": {"manifest_fingerprint": SHA_B},
    }
    if changed == "commit":
        values["commit_sha"] = GIT_B
    elif changed == "bundle_version":
        values["bundle_version"] = "agent-v3"
    elif changed == "bundle_hash":
        values["bundle_sha256"] = SHA_B
    else:
        values["rag_index_manifest"] = {"manifest_fingerprint": SHA_A}
    with pytest.raises(BehaviorBaselineError, match="environment changed"):
        verify_replay_environment(_manifest(), **values)


def test_manifest_checksum_and_dataset_classification_fail_closed():
    tampered = copy.deepcopy(_manifest())
    tampered["route_measurements"]["knowledge_qa"]["count"] = 999
    with pytest.raises(BehaviorBaselineError, match="checksum mismatch"):
        verify_replay_environment(
            tampered,
            commit_sha=GIT_A,
            bundle_version="agent-v2",
            bundle_sha256=SHA_A,
            rag_index_manifest={"manifest_fingerprint": SHA_B},
        )

    with pytest.raises(BehaviorBaselineError, match="classification"):
        build_behavior_baseline(
            baseline_id="bad",
            created_at="2026-09-02T00:00:00Z",
            commit_sha=GIT_A,
            tree_sha=GIT_B,
            bundle={"version": "agent-v2", "content_hash": SHA_A},
            model_policy={},
            rag_index_manifest={},
            datasets=[{
                "dataset_id": "bad", "version": "1", "classification": "gold",
                "manifest_path": "bad", "manifest_sha256": SHA_A,
                "cases_sha256": SHA_B,
            }],
            records=[_record()],
        )


def test_frozen_intent_and_rag_decision_policy_matches_current_owners():
    policy = DECISION_POLICY_BASELINE_V1
    assert policy["intent_fusion"]["ngram_weights"] == _VOTE_WEIGHTS["ngram"]
    assert policy["intent_fusion"]["disabled_weights"] == _VOTE_WEIGHTS["disabled"]
    rag = policy["knowledge_retrieval"]
    assert rag["raw_query_weight"] == DEFAULT_RAG_RETRIEVAL_POLICY["raw_query_weight"]
    assert rag["standalone_query_weight"] == DEFAULT_RAG_RETRIEVAL_POLICY[
        "standalone_query_weight"
    ]
    assert rag["dense_weight"] == DEFAULT_RAG_RETRIEVAL_POLICY["vector_weight"]
    assert rag["bm25_weight"] == DEFAULT_RAG_RETRIEVAL_POLICY["lexical_weight"]
    assert rag["rrf_k"] == DEFAULT_RAG_RETRIEVAL_POLICY["rrf_k"]
