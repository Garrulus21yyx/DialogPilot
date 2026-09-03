from __future__ import annotations

from pathlib import Path

from evaluation.rag_fusion_replay import FUSION_CONFIGS, replay_fusion_grid
from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase, RagDocument
from evaluation.rag_pipeline.dataset import RagDataset


def test_one_source_capture_replays_predeclared_grid_and_selects_lexically():
    content = "Refunds arrive in five business days."
    dataset = RagDataset(
        root=Path("."),
        manifest={"dataset_id": "fusion-dev-v1"},
        documents=(RagDocument("refund", "Refund timing", content),),
        cases=(
            RagCase(
                case_id="refund-1",
                group_id="refund",
                split="dev",
                query="refund timing",
                evidence=(EvidenceSpan("refund", 0, len(content), content),),
            ),
        ),
    )
    noise_ids = [f"a-noise-{index:02d}" for index in range(20)]
    capture = [
        {
            "case_id": "refund-1",
            "retrieval_status": "OK",
            "detail_code": None,
            "source_rankings": {
                "raw:lexical": ["z-gold"],
                "raw:vector": noise_ids,
            },
            "candidates": [
                {
                    "chunk_id": "z-gold",
                    "document_id": "refund",
                    "source_start_char": 0,
                    "source_end_char": len(content),
                },
                *(
                    {
                        "chunk_id": chunk_id,
                        "document_id": "noise",
                        "source_start_char": 0,
                        "source_end_char": 1,
                    }
                    for chunk_id in noise_ids
                ),
            ],
        }
    ]

    report = replay_fusion_grid(
        dataset=dataset,
        split="dev",
        capture_rows=capture,
        run_id="run-1",
    )

    assert len(FUSION_CONFIGS) == 15
    assert len(report["configs"]) == 15
    assert report["selected_config"] == {
        "config_id": "lexical-075-dense-025-rrf-10",
        "lexical_weight": 0.75,
        "dense_weight": 0.25,
        "rrf_k": 10,
    }
    by_id = {item["config_id"]: item for item in report["configs"]}
    assert by_id["lexical-100-dense-000-rrf-10"]["mrr_at_20"] == 1.0
    assert (
        by_id["lexical-000-dense-100-rrf-10"]["all_evidence_recall_at_20"]["rate"]
        == 0.0
    )
