from __future__ import annotations

import json
from pathlib import Path

from core.model_policy import ModelPolicy
from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase, RagDocument
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_query_replay import QUERY_CONFIGS, replay_query_grid
from evaluation.reused_query_capture import (
    load_reused_standalone_capture,
    rewrite_status,
)


def _dataset() -> RagDataset:
    first = "First policy evidence."
    second = "Second policy evidence."
    return RagDataset(
        root=Path("."),
        manifest={"dataset_id": "query-dev-v1"},
        documents=(
            RagDocument("first", "First", first),
            RagDocument("second", "Second", second),
        ),
        cases=(
            RagCase(
                case_id="case-1",
                group_id="group-1",
                split="dev",
                query="what about the first one?",
                history=("Tell me about the first policy.",),
                evidence=(EvidenceSpan("first", 0, len(first), first),),
            ),
            RagCase(
                case_id="case-2",
                group_id="group-2",
                split="dev",
                query="second policy",
                history=("Now another question.",),
                evidence=(EvidenceSpan("second", 0, len(second), second),),
            ),
        ),
    )


def test_query_replay_uses_standalone_only_when_production_would_accept_it():
    dataset = _dataset()
    rows = [
        {
            "case_id": "case-1",
            "retrieval_status": "OK",
            "rewrite_status": "REWRITTEN",
            "source_rankings": {
                "raw:vector": ["noise"],
                "standalone:vector": ["first-gold"],
            },
            "candidates": [
                _hit("noise", "noise", 1),
                _hit("first-gold", "first", len("First policy evidence.")),
            ],
        },
        {
            "case_id": "case-2",
            "retrieval_status": "OK",
            "rewrite_status": "FALLBACK_IDENTICAL",
            "source_rankings": {
                "raw:vector": ["second-gold"],
                "standalone:vector": [],
            },
            "candidates": [
                _hit("second-gold", "second", len("Second policy evidence.")),
            ],
        },
    ]

    report = replay_query_grid(
        dataset=dataset,
        capture_rows=rows,
        run_id="query-replay-run",
    )

    assert len(QUERY_CONFIGS) == 4
    assert report["selected_config"] == {
        "config_id": "raw-000-standalone-100",
        "raw_weight": 0.0,
        "standalone_weight": 1.0,
    }
    by_id = {item["config_id"]: item for item in report["configs"]}
    assert by_id["raw-100-standalone-000"]["all_evidence_recall_at_20"] == {
        "passed": 1,
        "total": 2,
        "rate": 0.5,
    }
    assert by_id["raw-000-standalone-100"]["all_evidence_recall_at_20"] == {
        "passed": 2,
        "total": 2,
        "rate": 1.0,
    }


def test_reused_capture_is_bound_to_dev_dataset_and_current_rewrite_profile(
    tmp_path,
):
    dataset = _dataset()
    rows = [
        {
            "case_id": case.case_id,
            "group_id": case.group_id,
            "history_turns": len(case.history),
            "raw_query": case.query,
            "standalone": (
                "standalone first policy" if case.case_id == "case-1" else case.query
            ),
            "errors": [],
        }
        for case in dataset.cases
    ]
    artifact = tmp_path / "query-transform-capture.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "query-dev-v1",
                "split": "dev",
                "sample_policy": (
                    "one maximum-history turn per dialogue, domain round-robin"
                ),
                "case_count": 2,
                "prompt_version": "customer-support-query-v1",
                "model_policy": {
                    "provider": "deepseek",
                    "base_url": "https://api.deepseek.com/anthropic",
                    "rewrite": {
                        "model": "deepseek-v4-flash",
                        "reasoning": "none",
                        "min_completion_tokens": 0,
                    },
                },
                "total_calls": 2,
                "total_input_tokens": 10,
                "total_output_tokens": 5,
                "error_case_count": 0,
                "rows": rows,
            }
        ),
        encoding="utf-8",
    )
    policy = ModelPolicy.from_env(
        {
            "MODEL_PROVIDER": "deepseek",
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "MODEL_REWRITE": "deepseek-v4-flash",
            "MODEL_REWRITE_REASONING": "none",
        }
    )

    capture = load_reused_standalone_capture(
        artifact,
        dataset=dataset,
        model_policy=policy,
    )

    assert len(capture.rows) == 2
    assert [rewrite_status(row) for row in capture.rows] == [
        "REWRITTEN",
        "FALLBACK_IDENTICAL",
    ]


def _hit(chunk_id: str, document_id: str, end: int) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "source_start_char": 0,
        "source_end_char": end,
    }
