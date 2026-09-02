"""Deterministic same-query legacy/new ServiceEpisode shadow attribution."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from application.hybrid_retrieval import HybridRetrievalResult, RetrievalStatus


@dataclass(frozen=True)
class EpisodeQueryCapture:
    capture_id: str
    query_sha256: str
    legacy: HybridRetrievalResult
    target: HybridRetrievalResult
    legacy_policy_fingerprint: str
    target_policy_fingerprint: str
    legacy_freshness: tuple[tuple[str, str], ...] = ()
    target_freshness: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.capture_id.strip() or len(self.query_sha256) != 64:
            raise ValueError("episode query capture identity is invalid")
        if any(len(item) != 64 for item in (
            self.legacy_policy_fingerprint, self.target_policy_fingerprint,
        )):
            raise ValueError("episode query capture policy is invalid")
        for values in (self.legacy_freshness, self.target_freshness):
            if len({item[0] for item in values}) != len(values) or any(
                not source.strip() or not timestamp.strip()
                for source, timestamp in values
            ):
                raise ValueError("episode query capture freshness is invalid")


def capture_query(query: str) -> tuple[str, str]:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    return f"episode-query:{digest}", digest


def compare_capture(capture: EpisodeQueryCapture) -> dict[str, object]:
    legacy_routes = _routes(capture.legacy)
    target_routes = _routes(capture.target)
    legacy_sources = _source_union(legacy_routes)
    target_sources = _source_union(target_routes)
    if capture.legacy.status is RetrievalStatus.UNAVAILABLE or (
        capture.target.status is RetrievalStatus.UNAVAILABLE
    ):
        classification = "UNAVAILABLE"
    elif capture.legacy.status is RetrievalStatus.CONFLICT or (
        capture.target.status is RetrievalStatus.CONFLICT
    ):
        classification = "CONFLICT"
    elif legacy_sources != target_sources:
        classification = "CORPUS_DIFFERENCE"
    elif legacy_routes != target_routes:
        classification = "RANKING_DIFFERENCE"
    else:
        classification = "MATCH"
    payload = {
        "capture_id": capture.capture_id,
        "query_sha256": capture.query_sha256,
        "legacy_status": capture.legacy.status.value,
        "target_status": capture.target.status.value,
        "legacy_policy_fingerprint": capture.legacy_policy_fingerprint,
        "target_policy_fingerprint": capture.target_policy_fingerprint,
        "legacy_generation": capture.legacy.generation_id,
        "target_generation": capture.target.generation_id,
        "legacy_source_ranks": legacy_routes,
        "target_source_ranks": target_routes,
        "classification": classification,
        "continuity_match": legacy_sources == target_sources,
        "provenance_match": _provenance(capture.legacy) == _provenance(capture.target),
        "freshness_comparable": bool(
            capture.legacy_freshness and capture.target_freshness
        ),
        "freshness_match": (
            dict(capture.legacy_freshness) == dict(capture.target_freshness)
            if capture.legacy_freshness and capture.target_freshness else None
        ),
    }
    payload["comparison_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return payload


def _routes(result: HybridRetrievalResult) -> dict[str, list[str]]:
    return {
        "dense": [item.source_id for item in result.dense_candidates],
        "lexical": [item.source_id for item in result.lexical_candidates],
    }


def _source_union(routes: dict[str, list[str]]) -> set[str]:
    return set(routes["dense"]) | set(routes["lexical"])


def _provenance(result: HybridRetrievalResult) -> dict[str, str]:
    return {
        item.source_id: item.provenance_sha256
        for item in (*result.dense_candidates, *result.lexical_candidates)
    }
