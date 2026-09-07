"""Versioned JSONL dataset contract for evidence-grounded RAG experiments."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase, RagDocument


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    # JSONL uses physical newlines. str.splitlines() also splits valid JSON
    # string contents such as U+0085/U+2028/U+2029 and corrupts source text.
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class RagDataset:
    root: Path
    manifest: Mapping[str, Any]
    documents: tuple[RagDocument, ...]
    cases: tuple[RagCase, ...]

    @classmethod
    def load(cls, root: Path | str, *, verify_checksum: bool = True) -> "RagDataset":
        root = Path(root)
        manifest_path = root / "manifest.json"
        corpus_path = root / "corpus.jsonl"
        cases_path = root / "cases.jsonl"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if verify_checksum:
            for key, path in (("corpus_sha256", corpus_path), ("cases_sha256", cases_path)):
                if str(manifest.get(key) or "") != _sha256(path):
                    raise ValueError(f"{path.name} checksum mismatch")
        documents = tuple(RagDocument(
            document_id=str(row["id"]),
            title=str(row.get("title") or ""),
            content=str(row["content"]),
            metadata=dict(row.get("metadata") or {}),
        ) for row in _read_jsonl(corpus_path))
        cases = tuple(_case_from_dict(row) for row in _read_jsonl(cases_path))
        dataset = cls(root, manifest, documents, cases)
        dataset.validate()
        return dataset

    def validate(self) -> None:
        documents = {document.document_id: document for document in self.documents}
        if len(documents) != len(self.documents):
            raise ValueError("duplicate document IDs")
        case_ids = set()
        group_splits: dict[str, str] = {}
        for case in self.cases:
            if case.case_id in case_ids:
                raise ValueError(f"duplicate case ID: {case.case_id}")
            case_ids.add(case.case_id)
            prior = group_splits.setdefault(case.group_id, case.split)
            if prior != case.split:
                raise ValueError(f"group crosses split boundary: {case.group_id}")
            for evidence in case.evidence:
                document = documents.get(evidence.document_id)
                if document is None:
                    raise ValueError(f"missing evidence document: {evidence.document_id}")
                if evidence.end_char > len(document.content):
                    raise ValueError(f"evidence span exceeds document: {case.case_id}")
                actual = document.content[evidence.start_char:evidence.end_char]
                if evidence.quote and actual != evidence.quote:
                    raise ValueError(f"evidence quote mismatch: {case.case_id}")
        if int(self.manifest.get("document_count", -1)) != len(self.documents):
            raise ValueError("manifest document_count mismatch")
        if int(self.manifest.get("case_count", -1)) != len(self.cases):
            raise ValueError("manifest case_count mismatch")

    def select_cases(self, split: str) -> tuple[RagCase, ...]:
        return tuple(case for case in self.cases if case.split == split)


def _case_from_dict(row: Mapping[str, Any]) -> RagCase:
    return RagCase(
        case_id=str(row["id"]),
        group_id=str(row["group_id"]),
        split=str(row["split"]),
        query=str(row["query"]),
        history=tuple(map(str, row.get("history") or ())),
        evidence=tuple(EvidenceSpan(
            document_id=str(item["document_id"]),
            start_char=int(item["start_char"]),
            end_char=int(item["end_char"]),
            quote=str(item.get("quote") or ""),
            relevance=int(item.get("relevance", 3)),
            granularity=str(item.get("granularity") or "span"),
        ) for item in row.get("evidence") or ()),
        query_types=tuple(map(str, row.get("query_types") or ())),
        required_claims=tuple(map(str, row.get("required_claims") or ())),
        forbidden_claims=tuple(map(str, row.get("forbidden_claims") or ())),
        answerable=bool(row.get("answerable", True)),
    )


def write_dataset(
    root: Path | str,
    *,
    dataset_id: str,
    documents: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
    source: Mapping[str, Any],
) -> None:
    """Write deterministic JSONL and a checksum-pinned manifest."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    corpus_path = root / "corpus.jsonl"
    cases_path = root / "cases.jsonl"
    _write_jsonl(corpus_path, documents)
    _write_jsonl(cases_path, cases)
    manifest = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "document_count": len(documents),
        "case_count": len(cases),
        "corpus_sha256": _sha256(corpus_path),
        "cases_sha256": _sha256(cases_path),
        "source": dict(source),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
