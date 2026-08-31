"""Build a bounded evidence-grounded RAG subset from the official Doc2Dial archive."""
from __future__ import annotations

import argparse
import json
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from evaluation.rag_pipeline.dataset import RagDataset, write_dataset


DOC2DIAL_URL = "https://doc2dial.github.io/file/doc2dial_v1.0.1.zip"
DOMAINS = ("dmv", "ssa", "studentaid", "va")


def _load_archive(archive: Path, member: str) -> Mapping[str, Any]:
    with zipfile.ZipFile(archive) as bundle:
        with bundle.open(member) as source:
            return json.load(source)


def build_subset(
    archive: Path,
    output: Path,
    *,
    max_documents: int = 100,
    max_cases: int = 300,
    split: str = "dev",
) -> RagDataset:
    if max_documents < len(DOMAINS) or max_cases < len(DOMAINS):
        raise ValueError("limits must allow at least one document and case per domain")
    docs_by_domain = _load_archive(archive, "doc2dial_doc.json")["doc_data"]
    dialogue_file = {
        "dev": "doc2dial_dial_validation.json",
        "heldout": "doc2dial_dial_test.json",
    }[split]
    dials_by_domain = _load_archive(archive, dialogue_file)["dial_data"]

    per_domain_case_limit = max(1, max_cases // len(DOMAINS))
    cases: list[dict[str, Any]] = []
    relevant_doc_ids: dict[str, set[str]] = defaultdict(set)
    for domain in DOMAINS:
        domain_cases = 0
        for doc_id in sorted(dials_by_domain.get(domain, {})):
            document = docs_by_domain[domain][doc_id]
            spans = document["spans"]
            for dialogue in sorted(
                dials_by_domain[domain][doc_id], key=lambda item: str(item["dial_id"]),
            ):
                turns = dialogue["turns"]
                for index, turn in enumerate(turns[:-1]):
                    next_turn = turns[index + 1]
                    if turn["role"] != "user" or next_turn["role"] != "agent":
                        continue
                    evidence = []
                    for reference in next_turn.get("references") or ():
                        span = spans.get(str(reference["sp_id"]))
                        if not span:
                            continue
                        evidence.append({
                            "document_id": doc_id,
                            "start_char": int(span["start_sp"]),
                            "end_char": int(span["end_sp"]),
                            "quote": span["text_sp"],
                            "relevance": 3,
                        })
                    if not evidence:
                        continue
                    history = [str(item["utterance"]) for item in turns[:index]]
                    cases.append({
                        "id": f"doc2dial-{split}-{dialogue['dial_id']}-{turn['turn_id']}",
                        "group_id": f"doc2dial-{dialogue['dial_id']}",
                        "split": split,
                        "query": str(turn["utterance"]),
                        "history": history,
                        "evidence": evidence,
                        "query_types": [
                            "customer_support",
                            "multi_turn" if history else "standalone",
                            domain,
                        ],
                        "required_claims": [str(next_turn["utterance"])],
                        "forbidden_claims": [],
                        "answerable": True,
                    })
                    relevant_doc_ids[domain].add(doc_id)
                    domain_cases += 1
                    if domain_cases >= per_domain_case_limit:
                        break
                if domain_cases >= per_domain_case_limit:
                    break
            if domain_cases >= per_domain_case_limit:
                break

    per_domain_doc_limit = max(1, max_documents // len(DOMAINS))
    documents: list[dict[str, Any]] = []
    for domain in DOMAINS:
        chosen = list(sorted(relevant_doc_ids[domain]))[:per_domain_doc_limit]
        for doc_id in sorted(docs_by_domain[domain]):
            if len(chosen) >= per_domain_doc_limit:
                break
            if doc_id not in relevant_doc_ids[domain]:
                chosen.append(doc_id)
        for doc_id in chosen:
            document = docs_by_domain[domain][doc_id]
            documents.append({
                "id": doc_id,
                "title": str(document.get("title") or ""),
                "content": str(document["doc_text"]),
                "metadata": {"domain": domain, "source": "doc2dial-v1.0.1"},
            })

    selected_ids = {document["id"] for document in documents}
    cases = [
        case for case in cases
        if all(item["document_id"] in selected_ids for item in case["evidence"])
    ][:max_cases]
    write_dataset(
        output,
        dataset_id=f"doc2dial-rag-mini-{split}-v1",
        documents=documents,
        cases=cases,
        source={
            "dataset": "Doc2Dial v1.0.1",
            "url": DOC2DIAL_URL,
            "license": "CC-BY-3.0",
            "selection": "deterministic domain-balanced prefix plus distractors",
        },
    )
    return RagDataset.load(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "heldout"), default="dev")
    parser.add_argument("--max-documents", type=int, default=100)
    parser.add_argument("--max-cases", type=int, default=300)
    args = parser.parse_args()
    archive = args.archive
    if archive is None:
        with tempfile.TemporaryDirectory(prefix="dialogpilot-doc2dial-") as temp_dir:
            archive = Path(temp_dir) / "doc2dial.zip"
            urllib.request.urlretrieve(DOC2DIAL_URL, archive)
            dataset = build_subset(
                archive, args.output,
                max_documents=args.max_documents,
                max_cases=args.max_cases,
                split=args.split,
            )
    else:
        dataset = build_subset(
            archive, args.output,
            max_documents=args.max_documents,
            max_cases=args.max_cases,
            split=args.split,
        )
    print(json.dumps({
        "dataset_id": dataset.manifest["dataset_id"],
        "documents": len(dataset.documents),
        "cases": len(dataset.cases),
        "output": str(dataset.root),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
