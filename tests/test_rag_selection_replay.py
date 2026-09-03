from __future__ import annotations

import hashlib
import json
from argparse import Namespace

import pytest

from evaluation.rag_pipeline.dataset import write_dataset
from mcp.source_document import SourceDocument
from scripts.run_rag_selection_eval import run


def test_frozen_candidates_flow_through_identity_packing_and_evidence_pack(tmp_path):
    dataset, query_run = _fixture(tmp_path)
    output = tmp_path / "selection"

    report = run(
        Namespace(dataset=dataset, query_run=query_run, output=output),
        run_id="selection-run",
    )

    assert report["selected_config"] == {
        "config_id": "top-05-budget-1800",
        "final_k": 5,
        "context_max_tokens": 1800,
    }
    assert report["candidate"]["all_evidence_recall_at_20"]["rate"] == 1.0
    by_id = {item["config_id"]: item for item in report["configs"]}
    assert by_id["top-03-budget-1800"]["packed"]["all_evidence_recall"]["rate"] == 0.0
    assert by_id["top-05-budget-1800"]["packed"]["all_evidence_recall"]["rate"] == 1.0

    manifest = _read_json(output / "manifest.json")
    prediction = _read_jsonl(output / "predictions.jsonl")[0]
    assert manifest["evaluation_role"] == "VIEWED_DEV_SELECTION_PACKING"
    assert manifest["promotion_allowed"] is False
    assert set(manifest["runtime_invocations"].values()) == {0}
    assert set(manifest["stages_not_run"]) == {
        "postgres",
        "embedding_model",
        "live_query_rewrite",
        "model_rerank",
        "parent_expansion",
        "generation",
        "judge",
    }
    assert set(path.name for path in output.iterdir()) == {
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    }
    top_five = next(
        item
        for item in prediction["configurations"]
        if item["config_id"] == "top-05-budget-1800"
    )
    assert len(top_five["evidence_pack"]["items"]) == 5
    assert "text" not in top_five["evidence_pack"]["items"][0]
    assert top_five["evidence_pack"]["rerank_prompt_version"] == (
        "identity-no-rerank-v1"
    )


def test_replay_rejects_candidate_source_binding_drift(tmp_path):
    dataset, query_run = _fixture(tmp_path)
    predictions_path = query_run / "predictions.jsonl"
    row = _read_jsonl(predictions_path)[0]
    row["candidates"][0]["source_checksum"] = "0" * 64
    _write_jsonl(predictions_path, [row])
    capture_sha = _sha256(predictions_path)
    for name in ("manifest.json", "report.json"):
        path = query_run / name
        value = _read_json(path)
        value["source_capture"]["sha256"] = capture_sha
        _write_json(path, value)

    with pytest.raises(ValueError, match="candidate source binding drift"):
        run(
            Namespace(
                dataset=dataset,
                query_run=query_run,
                output=tmp_path / "selection",
            )
        )


def _fixture(tmp_path):
    dataset = tmp_path / "dataset"
    documents = [
        {
            "id": f"doc-{index}",
            "title": f"Document {index}",
            "content": f"Evidence text for document {index}. " * 4,
        }
        for index in range(1, 9)
    ]
    gold = documents[3]
    write_dataset(
        dataset,
        dataset_id="selection-dev-v1",
        documents=documents,
        cases=[
            {
                "id": "case-1",
                "group_id": "dialogue-1",
                "split": "dev",
                "query": "what was the fourth policy?",
                "history": ["We discussed several policies."],
                "evidence": [
                    {
                        "document_id": gold["id"],
                        "start_char": 0,
                        "end_char": len(gold["content"]),
                        "quote": gold["content"],
                    }
                ],
            }
        ],
        source={"dataset": "test"},
    )
    dataset_manifest = _read_json(dataset / "manifest.json")
    candidate_ids = [f"chunk-{index}" for index in range(1, 9)]
    candidates = []
    for candidate_id, document in zip(candidate_ids, documents, strict=True):
        source = SourceDocument.create(
            source_id=document["id"],
            title=document["title"],
            content=document["content"],
        )
        candidates.append(
            {
                "chunk_id": candidate_id,
                "document_id": document["id"],
                "source_checksum": source.checksum,
                "source_revision": source.revision_id,
                "source_start_char": 0,
                "source_end_char": len(document["content"]),
            }
        )
    query_run = tmp_path / "query"
    query_run.mkdir()
    _write_jsonl(
        query_run / "predictions.jsonl",
        [
            {
                "case_id": "case-1",
                "rewrite_status": "REWRITTEN",
                "raw_query": "what was the fourth policy?",
                "standalone": "fourth policy evidence",
                "source_rankings": {
                    "raw:vector": list(reversed(candidate_ids)),
                    "standalone:vector": candidate_ids,
                },
                "candidates": candidates,
            }
        ],
    )
    capture_sha = _sha256(query_run / "predictions.jsonl")
    selected = {
        "config_id": "raw-000-standalone-100",
        "raw_weight": 0.0,
        "standalone_weight": 1.0,
    }
    _write_json(
        query_run / "manifest.json",
        {
            "run_id": "query-run",
            "dataset": {
                "dataset_id": dataset_manifest["dataset_id"],
                "split": "dev",
                "corpus_sha256": dataset_manifest["corpus_sha256"],
                "cases_sha256": dataset_manifest["cases_sha256"],
            },
            "cohort": {"case_count": 1},
            "offline_replay": {"configs": [selected]},
            "source_capture": {"sha256": capture_sha},
            "chunk_profile": {"profile_id": "fixed-512-64"},
            "embedding_profile": {"model": "BAAI/bge-m3"},
            "generation": {"manifest_fingerprint": "manifest-v1"},
        },
    )
    _write_json(
        query_run / "report.json",
        {
            "case_count": 1,
            "selected_config": selected,
            "source_capture": {"sha256": capture_sha},
        },
    )
    return dataset, query_run


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path, values):
    path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in values),
        encoding="utf-8",
    )
