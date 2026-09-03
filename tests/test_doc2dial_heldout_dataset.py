from __future__ import annotations

import json
import zipfile

from evaluation.doc2dial_heldout_dataset import (
    DOMAINS,
    freeze_doc2dial_heldout,
)
from evaluation.rag_pipeline.dataset import RagDataset, write_dataset


def test_freeze_balances_domains_and_lengths_without_reusing_conversations(tmp_path):
    archive = tmp_path / "doc2dial.zip"
    _write_archive(archive)
    excluded_path = tmp_path / "excluded"
    write_dataset(
        excluded_path,
        dataset_id="old-diagnostic",
        documents=[{"id": "old", "title": "Old", "content": "old evidence"}],
        cases=[
            {
                "id": "old-case",
                "group_id": "doc2dial-old-dialogue",
                "split": "heldout",
                "query": "old",
                "history": [],
                "evidence": [
                    {
                        "document_id": "old",
                        "start_char": 0,
                        "end_char": 12,
                        "quote": "old evidence",
                    }
                ],
            }
        ],
        source={"role": "diagnostic"},
    )
    excluded = RagDataset.load(excluded_path)

    first = freeze_doc2dial_heldout(
        archive,
        tmp_path / "first",
        excluded_dataset=excluded,
        per_stratum=1,
        expected_archive_sha256=None,
    )
    second = freeze_doc2dial_heldout(
        archive,
        tmp_path / "second",
        excluded_dataset=excluded,
        per_stratum=1,
        expected_archive_sha256=None,
    )

    assert len(first.documents) == 12
    assert len(first.cases) == 12
    assert len({case.group_id for case in first.cases}) == 12
    assert "doc2dial-old-dialogue" not in {case.group_id for case in first.cases}
    assert first.manifest["corpus_sha256"] == second.manifest["corpus_sha256"]
    assert first.manifest["cases_sha256"] == second.manifest["cases_sha256"]
    assert first.manifest["source"]["strata_counts"] == {
        f"{domain}/{bucket}": 1
        for domain in DOMAINS
        for bucket in ("short", "medium", "long")
    }
    assert all(len(case.history) == 2 for case in first.cases)
    for case in first.cases:
        evidence = case.evidence[0]
        document = next(
            item for item in first.documents if item.document_id == evidence.document_id
        )
        assert (
            document.content[evidence.start_char : evidence.end_char] == evidence.quote
        )


def _write_archive(path):
    doc_data = {domain: {} for domain in DOMAINS}
    dial_data = {domain: {} for domain in DOMAINS}
    lengths = {"short": 1_000, "medium": 5_000, "long": 9_000}
    for domain in DOMAINS:
        for bucket, length in lengths.items():
            document_id = f"{domain}-{bucket}"
            evidence = f"{domain} {bucket} evidence"
            content = evidence + " x" * ((length - len(evidence)) // 2 + 1)
            content = content[:length]
            doc_data[domain][document_id] = {
                "title": document_id,
                "doc_text": content,
                "spans": {
                    "1": {
                        "start_sp": 0,
                        "end_sp": len(evidence),
                        "text_sp": evidence,
                    }
                },
            }
            dialogue_id = f"{domain}-{bucket}-dialogue"
            dial_data[domain][document_id] = [
                _dialogue(dialogue_id, evidence),
                *(
                    [_dialogue("old-dialogue", evidence)]
                    if domain == "dmv" and bucket == "short"
                    else []
                ),
            ]
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("doc2dial_doc.json", json.dumps({"doc_data": doc_data}))
        bundle.writestr("doc2dial_dial_test.json", json.dumps({"dial_data": dial_data}))


def _dialogue(dialogue_id, evidence):
    return {
        "dial_id": dialogue_id,
        "turns": [
            {"turn_id": 1, "role": "user", "utterance": "Initial question"},
            {
                "turn_id": 2,
                "role": "agent",
                "utterance": evidence,
                "references": [{"sp_id": "1"}],
            },
            {"turn_id": 3, "role": "user", "utterance": "And that one?"},
            {
                "turn_id": 4,
                "role": "agent",
                "utterance": evidence,
                "references": [{"sp_id": "1"}],
            },
        ],
    }
