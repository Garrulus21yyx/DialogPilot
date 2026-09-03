"""Freeze the untouched Doc2Dial test pool into one bounded heldout dataset."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from evaluation.rag_pipeline.dataset import RagDataset, write_dataset


DOC2DIAL_ARCHIVE_SHA256 = (
    "94499fa5259f69018d2458cb948552e7a05f424711a95a562bb9e816a515dc23"
)
DOC2DIAL_URL = "https://doc2dial.github.io/file/doc2dial_v1.0.1.zip"
DATASET_ID = "doc2dial-rag-en-heldout-balanced-v1"
SELECTION_SEED = "doc2dial-en-heldout-balanced-v1"
DOMAINS = ("dmv", "ssa", "studentaid", "va")
LENGTH_BUCKETS = ("short", "medium", "long")


def freeze_doc2dial_heldout(
    archive: Path,
    output: Path,
    *,
    excluded_dataset: RagDataset,
    per_stratum: int = 10,
    expected_archive_sha256: str | None = DOC2DIAL_ARCHIVE_SHA256,
) -> RagDataset:
    """Select one maximum-history turn per conversation in 12 fixed strata."""
    if per_stratum < 1:
        raise ValueError("per_stratum must be positive")
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected_archive_sha256 and archive_sha256 != expected_archive_sha256:
        raise ValueError("Doc2Dial archive checksum mismatch")

    docs_by_domain = _load_member(archive, "doc2dial_doc.json")["doc_data"]
    dials_by_domain = _load_member(archive, "doc2dial_dial_test.json")["dial_data"]
    if set(docs_by_domain) != set(DOMAINS) or set(dials_by_domain) != set(DOMAINS):
        raise ValueError("Doc2Dial domain set drift")

    excluded_groups = {case.group_id for case in excluded_dataset.cases}
    by_stratum: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for domain in DOMAINS:
        for document_id in sorted(dials_by_domain[domain]):
            document = docs_by_domain[domain][document_id]
            bucket = document_length_bucket(len(str(document["doc_text"])))
            for dialogue in dials_by_domain[domain][document_id]:
                group_id = f"doc2dial-{dialogue['dial_id']}"
                if group_id in excluded_groups:
                    continue
                case = _maximum_history_case(
                    domain=domain,
                    bucket=bucket,
                    document_id=document_id,
                    document=document,
                    dialogue=dialogue,
                )
                if case is not None:
                    by_stratum[(domain, bucket)].append(case)

    cases: list[dict[str, Any]] = []
    strata_counts: dict[str, int] = {}
    for domain in DOMAINS:
        for bucket in LENGTH_BUCKETS:
            key = (domain, bucket)
            candidates = sorted(by_stratum[key], key=_selection_key)
            if len(candidates) < per_stratum:
                raise ValueError(
                    f"insufficient Doc2Dial cases for {domain}/{bucket}: "
                    f"{len(candidates)} < {per_stratum}"
                )
            selected = candidates[:per_stratum]
            cases.extend(selected)
            strata_counts[f"{domain}/{bucket}"] = len(selected)

    documents = [
        {
            "id": document_id,
            "title": str(document.get("title") or ""),
            "content": str(document["doc_text"]),
            "metadata": {
                "domain": domain,
                "source": "doc2dial-v1.0.1",
                "document_length_bucket": document_length_bucket(
                    len(str(document["doc_text"]))
                ),
            },
        }
        for domain in DOMAINS
        for document_id, document in sorted(docs_by_domain[domain].items())
    ]
    cases.sort(key=lambda item: str(item["id"]))
    expected_count = len(DOMAINS) * len(LENGTH_BUCKETS) * per_stratum
    if len(cases) != expected_count or len({item["group_id"] for item in cases}) != len(
        cases
    ):
        raise ValueError("heldout selection must contain one case per conversation")

    write_dataset(
        output,
        dataset_id=DATASET_ID,
        documents=documents,
        cases=cases,
        source={
            "dataset": "Doc2Dial v1.0.1",
            "url": DOC2DIAL_URL,
            "license": "CC-BY-3.0",
            "archive_sha256": archive_sha256,
            "official_split": "test",
            "locale": "en",
            "selection_profile": "conversation-domain-length-balanced-v1",
            "selection_seed": SELECTION_SEED,
            "selection_policy": (
                "one maximum-history answerable turn per conversation; "
                "SHA-256 order within domain/document-length stratum"
            ),
            "length_bucket_chars": {
                "short": "<4000",
                "medium": "4000-7999",
                "long": ">=8000",
            },
            "strata_counts": strata_counts,
            "grounding_granularity": "character-span",
            "corpus_policy": "all_official_documents",
            "excluded_dataset": {
                "dataset_id": str(excluded_dataset.manifest["dataset_id"]),
                "cases_sha256": str(excluded_dataset.manifest["cases_sha256"]),
                "group_count": len(excluded_groups),
                "group_ids_sha256": _text_sha256("\n".join(sorted(excluded_groups))),
            },
        },
    )
    return RagDataset.load(output, verify_checksum=True)


def document_length_bucket(characters: int) -> str:
    if characters < 4_000:
        return "short"
    if characters < 8_000:
        return "medium"
    return "long"


def _maximum_history_case(
    *,
    domain: str,
    bucket: str,
    document_id: str,
    document: Mapping[str, Any],
    dialogue: Mapping[str, Any],
) -> dict[str, Any] | None:
    turns = list(dialogue["turns"])
    spans = document["spans"]
    eligible: list[
        tuple[int, Mapping[str, Any], Mapping[str, Any], list[dict[str, Any]]]
    ] = []
    for index, turn in enumerate(turns[:-1]):
        next_turn = turns[index + 1]
        if turn["role"] != "user" or next_turn["role"] != "agent":
            continue
        evidence = []
        for reference in next_turn.get("references") or ():
            span = spans.get(str(reference["sp_id"]))
            if span is not None:
                evidence.append(
                    {
                        "document_id": document_id,
                        "start_char": int(span["start_sp"]),
                        "end_char": int(span["end_sp"]),
                        "quote": str(span["text_sp"]),
                        "relevance": 3,
                    }
                )
        if evidence:
            eligible.append((index, turn, next_turn, evidence))
    if not eligible:
        return None
    index, turn, next_turn, evidence = max(eligible, key=lambda item: item[0])
    dialogue_id = str(dialogue["dial_id"])
    return {
        "id": f"doc2dial-heldout-{dialogue_id}-{turn['turn_id']}",
        "group_id": f"doc2dial-{dialogue_id}",
        "split": "heldout",
        "query": str(turn["utterance"]),
        "history": [str(item["utterance"]) for item in turns[:index]],
        "evidence": evidence,
        "query_types": [
            "customer_support",
            "multi_turn" if index else "standalone",
            domain,
            f"{bucket}_document",
        ],
        "required_claims": [str(next_turn["utterance"])],
        "forbidden_claims": [],
        "answerable": True,
    }


def _selection_key(case: Mapping[str, Any]) -> tuple[str, str]:
    case_id = str(case["id"])
    return _text_sha256(f"{SELECTION_SEED}\0{case_id}"), case_id


def _load_member(archive: Path, member: str) -> Mapping[str, Any]:
    with zipfile.ZipFile(archive) as bundle, bundle.open(member) as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"Doc2Dial member is not an object: {member}")
    return value


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
