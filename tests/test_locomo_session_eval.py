from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.command_primary_eval.locomo_session_adapter import (
    LOCOMO_CASE_COUNT,
    LOCOMO_DOCUMENT_COUNT,
    LOCOMO_SAMPLE_ID,
    LocomoAdapterError,
    LocomoSourcePin,
    load_locomo_single_hop_slice,
)
from evaluation.command_primary_eval.locomo_session_contracts import (
    BenchmarkSessionDocument,
    RankedSessionHit,
)
from evaluation.command_primary_eval.locomo_session_eval import (
    CORPUS_SEMANTICS,
    PRODUCTION_SEMANTICS,
    evaluate_locomo_session_slice,
)


REVISION = "test-source-revision"


class MappingRetriever:
    version = "mapping-retriever-test-v1"

    def __init__(self, rankings: dict[str, tuple[str, ...]]) -> None:
        self._rankings = rankings
        self.calls = 0

    def retrieve(
        self,
        *,
        query: str,
        documents: tuple[BenchmarkSessionDocument, ...],
        top_k: int,
    ) -> tuple[RankedSessionHit, ...]:
        self.calls += 1
        known = {item.session_id for item in documents}
        assert set(self._rankings[query]).issubset(known)
        return tuple(
            RankedSessionHit(session_id, 1.0 / rank)
            for rank, session_id in enumerate(self._rankings[query][:top_k], start=1)
        )


def test_pinned_single_hop_slice_and_three_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "locomo10.json"
    source.write_text(
        json.dumps(_source_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    dataset = load_locomo_single_hop_slice(
        source,
        source=LocomoSourcePin(REVISION, digest),
    )

    assert len(dataset.documents) == LOCOMO_DOCUMENT_COUNT
    assert len(dataset.cases) == LOCOMO_CASE_COUNT
    assert dataset.documents[0].session_id == "S1"
    assert dataset.cases[0].gold_session_ids == ("S1",)
    assert dataset.cases[-1].gold_session_ids == ("S13",)

    rankings = {}
    all_sessions = tuple(item.session_id for item in dataset.documents)
    for index, case in enumerate(dataset.cases):
        gold = case.gold_session_ids[0]
        others = tuple(item for item in all_sessions if item != gold)
        rankings[case.question] = (
            (others[0], gold, *others[1:]) if index == 0 else (gold, *others)
        )
    retriever = MappingRetriever(rankings)
    output = tmp_path / "result"
    report = evaluate_locomo_session_slice(
        dataset=dataset,
        retriever=retriever,
        output_dir=output,
        run_id="locomo-session-test-run",
    )

    assert retriever.calls == LOCOMO_CASE_COUNT
    assert {item.name for item in output.iterdir()} == {
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    }
    manifest = json.loads((output / "manifest.json").read_text("utf-8"))
    predictions = [
        json.loads(line)
        for line in (output / "predictions.jsonl").read_text("utf-8").splitlines()
    ]
    persisted = json.loads((output / "report.json").read_text("utf-8"))

    assert manifest["corpus_semantics"] == CORPUS_SEMANTICS
    assert manifest["production_service_episode_semantics"] == PRODUCTION_SEMANTICS
    assert manifest["dataset"] == {
        "case_count": 70,
        "category": 4,
        "dataset_id": "locomo10",
        "document_count": 19,
        "document_unit": "session",
        "question_type": "single-hop",
        "sample_id": LOCOMO_SAMPLE_ID,
        "source_revision": REVISION,
        "source_sha256": digest,
    }
    assert len(predictions) == LOCOMO_CASE_COUNT
    assert predictions[0]["metrics"] == {
        "mrr": 0.5,
        "recall_all_at_5": 1.0,
    }
    assert report["metrics"]["recall_all_at_5"] == 1.0
    assert report["metrics"]["mrr"] == pytest.approx(69.5 / 70)
    assert report["retrieval_latency_ms"]["p95"] >= 0
    assert persisted == report


def test_source_checksum_is_mandatory(tmp_path: Path) -> None:
    source = tmp_path / "locomo10.json"
    source.write_text("[]", encoding="utf-8")

    with pytest.raises(LocomoAdapterError, match="checksum mismatch"):
        load_locomo_single_hop_slice(
            source,
            source=LocomoSourcePin(REVISION, "0" * 64),
        )


def _source_payload() -> list[dict[str, object]]:
    conversation: dict[str, object] = {
        "speaker_a": "Ada",
        "speaker_b": "Ben",
    }
    for session in range(1, LOCOMO_DOCUMENT_COUNT + 1):
        conversation[f"session_{session}"] = [
            {
                "speaker": "Ada",
                "dia_id": f"D{session}:1",
                "text": f"Synthetic fact {session}.",
            }
        ]
        conversation[f"session_{session}_date_time"] = f"{session} January, 2026"
    qa = [
        {
            "question": f"Which synthetic fact is number {index}?",
            "answer": f"Synthetic fact {((index - 1) % 19) + 1}",
            "evidence": [f"D{((index - 1) % 19) + 1}:1"],
            "category": 4,
        }
        for index in range(1, LOCOMO_CASE_COUNT + 1)
    ]
    qa.append(
        {
            "question": "Ignored temporal question",
            "answer": "Never",
            "evidence": ["D1:1"],
            "category": 2,
        }
    )
    return [
        {
            "sample_id": LOCOMO_SAMPLE_ID,
            "conversation": conversation,
            "qa": qa,
            "observation": {},
            "session_summary": {},
            "event_summary": {},
        }
    ]
