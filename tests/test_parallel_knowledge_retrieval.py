"""Real PostgreSQL paired branch results, including metadata routes."""

import asyncio
from dataclasses import replace

import pytest

from tests.test_postgres_knowledge_retriever import knowledge_source, _request
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource


@pytest.mark.parametrize("hints", [False, True])
@pytest.mark.parametrize("depths", [(20, 20), (0, 20), (20, 0)])
def test_parallel_matches_serial_source_rankings(knowledge_source, hints, depths):
    source = knowledge_source
    request = _request()
    if hints:
        request = replace(request, source_type_hints=("text",))
    parallel = PostgresKnowledgeCandidateSource(
        backend=source._backend,
        generations=source._generations,
        pool=source._pool,
        embed_query=source._embed_query,
        parallel=True,
    )

    async def capture(candidate):
        return await candidate.capture_source_rankings_async(
            request,
            [("raw", request.query, 1.0)],
            dense_k=depths[0],
            lexical_k=depths[1],
        )

    try:
        expected = asyncio.run(capture(source))
        actual = asyncio.run(capture(parallel))
        assert actual == expected
    finally:
        parallel.close()


def test_parallel_latency_capture(knowledge_source, tmp_path):
    """Optional diagnostic, no fragile claim that concurrency must be faster."""
    import json
    import os
    import statistics
    import time

    source = knowledge_source
    parallel = PostgresKnowledgeCandidateSource(
        backend=source._backend,
        generations=source._generations,
        pool=source._pool,
        embed_query=source._embed_query,
        parallel=True,
    )
    measurements = {"serial": [], "parallel": []}
    request = _request()
    try:
        for iteration in range(22):
            modes = [("serial", source), ("parallel", parallel)]
            if iteration % 2:
                modes.reverse()
            results = []
            for name, candidate in modes:
                start = time.perf_counter()
                results.append(
                    candidate._search(request, [("raw", request.query, 1.0)], 20)
                )
                if iteration >= 2:
                    measurements[name].append((time.perf_counter() - start) * 1000)
            assert results[0] == results[1]
        report = {
            "scope": "isolated PostgreSQL one-source fixture; constant embedding, not production load",
            "samples_per_mode": 20,
            "metrics": {
                name: {
                    "p50_ms": statistics.median(values),
                    "p95_ms": sorted(values)[18],
                }
                for name, values in measurements.items()
            },
            "raw_ms": measurements,
        }
        destination = os.getenv("RAG_PARALLEL_BENCHMARK_REPORT")
        if destination:
            from pathlib import Path

            Path(destination).write_text(json.dumps(report, indent=2) + "\n")
    finally:
        parallel.close()
