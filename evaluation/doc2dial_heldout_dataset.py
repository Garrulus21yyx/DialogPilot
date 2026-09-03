"""Freeze the untouched Doc2Dial test pool into one bounded heldout dataset."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.rag_pipeline.dataset import RagDataset, write_dataset


DOC2DIAL_ARCHIVE_SHA256 = (
    "94499fa5259f69018d2458cb948552e7a05f424711a95a562bb9e816a515dc23"
)
DOC2DIAL_URL = "https://doc2dial.github.io/file/doc2dial_v1.0.1.zip"
DATASET_ID = "doc2dial-rag-en-heldout-balanced-v1"
SELECTION_SEED = "doc2dial-en-heldout-balanced-v1"
DOMAINS = ("dmv", "ssa", "studentaid", "va")
LENGTH_BUCKETS = ("short", "medium", "long")
DOMAIN_BALANCED_SUPPLEMENT_QUOTAS = {
    ("dmv", "short"): 8,
    ("dmv", "medium"): 22,
    ("dmv", "long"): 0,
    ("ssa", "short"): 17,
    ("ssa", "medium"): 5,
    ("ssa", "long"): 8,
    ("studentaid", "short"): 8,
    ("studentaid", "medium"): 5,
    ("studentaid", "long"): 17,
    ("va", "short"): 15,
    ("va", "medium"): 13,
    ("va", "long"): 2,
}


def freeze_doc2dial_heldout(
    archive: Path,
    output: Path,
    *,
    excluded_dataset: RagDataset | Sequence[RagDataset],
    per_stratum: int = 10,
    expected_archive_sha256: str | None = DOC2DIAL_ARCHIVE_SHA256,
    dataset_id: str = DATASET_ID,
    selection_seed: str = SELECTION_SEED,
    stratum_quotas: Mapping[tuple[str, str], int] | None = None,
) -> RagDataset:
    """Select one maximum-history turn per conversation in 12 fixed strata."""
    if per_stratum < 1:
        raise ValueError("per_stratum must be positive")
    expected_strata = {
        (domain, bucket) for domain in DOMAINS for bucket in LENGTH_BUCKETS
    }
    quotas = (
        dict(stratum_quotas)
        if stratum_quotas is not None
        else {key: per_stratum for key in expected_strata}
    )
    if set(quotas) != expected_strata or any(value < 0 for value in quotas.values()):
        raise ValueError("stratum quotas must cover every domain/length bucket")
    if not sum(quotas.values()):
        raise ValueError("stratum quotas must select at least one case")
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected_archive_sha256 and archive_sha256 != expected_archive_sha256:
        raise ValueError("Doc2Dial archive checksum mismatch")

    docs_by_domain = _load_member(archive, "doc2dial_doc.json")["doc_data"]
    dials_by_domain = _load_member(archive, "doc2dial_dial_test.json")["dial_data"]
    if set(docs_by_domain) != set(DOMAINS) or set(dials_by_domain) != set(DOMAINS):
        raise ValueError("Doc2Dial domain set drift")

    excluded_datasets = (
        (excluded_dataset,)
        if isinstance(excluded_dataset, RagDataset)
        else tuple(excluded_dataset)
    )
    if not excluded_datasets:
        raise ValueError("at least one excluded dataset is required")
    excluded_groups = {
        case.group_id
        for dataset in excluded_datasets
        for case in dataset.cases
    }
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
            candidates = sorted(
                by_stratum[key],
                key=lambda case: _selection_key(case, selection_seed),
            )
            quota = quotas[key]
            if len(candidates) < quota:
                raise ValueError(
                    f"insufficient Doc2Dial cases for {domain}/{bucket}: "
                    f"{len(candidates)} < {quota}"
                )
            selected = candidates[:quota]
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
    expected_count = sum(quotas.values())
    if len(cases) != expected_count or len({item["group_id"] for item in cases}) != len(
        cases
    ):
        raise ValueError("heldout selection must contain one case per conversation")

    write_dataset(
        output,
        dataset_id=dataset_id,
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
            "selection_seed": selection_seed,
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
            "excluded_datasets": [{
                "dataset_id": str(dataset.manifest["dataset_id"]),
                "cases_sha256": str(dataset.manifest["cases_sha256"]),
                "group_count": len({case.group_id for case in dataset.cases}),
            } for dataset in excluded_datasets],
            "excluded_group_count": len(excluded_groups),
            "excluded_group_ids_sha256": _text_sha256(
                "\n".join(sorted(excluded_groups))
            ),
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


def _selection_key(
    case: Mapping[str, Any], selection_seed: str,
) -> tuple[str, str]:
    case_id = str(case["id"])
    return _text_sha256(f"{selection_seed}\0{case_id}"), case_id


def _load_member(archive: Path, member: str) -> Mapping[str, Any]:
    with zipfile.ZipFile(archive) as bundle, bundle.open(member) as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"Doc2Dial member is not an object: {member}")
    return value


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
