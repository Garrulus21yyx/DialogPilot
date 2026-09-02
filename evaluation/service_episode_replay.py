"""Deterministic, privacy-safe ServiceEpisode offline replay report."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ServiceEpisodeReplayCase:
    case_id: str
    query: str
    expected_episode_ids: tuple[str, ...]
    forbidden_episode_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.case_id.strip()
            or not self.query.strip()
            or not self.expected_episode_ids
            or any(not item.strip() for item in (
                *self.expected_episode_ids, *self.forbidden_episode_ids,
            ))
        ):
            raise ValueError("service episode replay case is incomplete")


def run_service_episode_replay(
    search,
    cases: Iterable[ServiceEpisodeReplayCase],
    *,
    tenant_id: str,
    user_id: str,
    top_k: int = 5,
) -> dict[str, object]:
    ordered = tuple(cases)
    if not ordered or len({item.case_id for item in ordered}) != len(ordered):
        raise ValueError("service episode replay cases must be unique and non-empty")
    rows = []
    recalled = 0
    expected_total = 0
    forbidden_leaks = 0
    for case in ordered:
        result = search.search(
            tenant_id=tenant_id, user_id=user_id, query=case.query, top_k=top_k,
        )
        hit_ids = tuple(item.episode_id for item in result.hits)
        expected_hits = len(set(hit_ids) & set(case.expected_episode_ids))
        leaks = tuple(sorted(set(hit_ids) & set(case.forbidden_episode_ids)))
        recalled += expected_hits
        expected_total += len(set(case.expected_episode_ids))
        forbidden_leaks += len(leaks)
        rows.append({
            "case_id": case.case_id,
            "query_sha256": hashlib.sha256(case.query.encode("utf-8")).hexdigest(),
            "status": result.status.value,
            "detail_code": result.detail_code,
            "expected_episode_ids": list(case.expected_episode_ids),
            "hit_episode_ids": list(hit_ids),
            "forbidden_leaks": list(leaks),
            "hits": [{
                "episode_id": item.episode_id,
                "episode_revision": item.episode_revision,
                "provenance_sha256": item.provenance_sha256,
                "source_ranks": dict(item.source_ranks),
                "freshness_at": item.freshness_at,
                "index_watermark": item.index_watermark,
                "policy_fingerprint": item.policy_fingerprint,
            } for item in result.hits],
        })
    recall = recalled / expected_total
    payload: dict[str, object] = {
        "schema_version": "service-episode-offline-replay-v1",
        "tenant_scope_sha256": hashlib.sha256(
            tenant_id.encode("utf-8")
        ).hexdigest(),
        "user_scope_sha256": hashlib.sha256(user_id.encode("utf-8")).hexdigest(),
        "case_count": len(rows),
        "expected_recall_at_k": recall,
        "forbidden_leak_count": forbidden_leaks,
        "result": "PASS" if recall == 1.0 and forbidden_leaks == 0 else "FAIL",
        "cases": rows,
    }
    payload["report_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return payload
