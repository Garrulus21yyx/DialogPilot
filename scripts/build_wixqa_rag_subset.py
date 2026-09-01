"""Build a small group-safe customer-support RAG suite from official WixQA JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.rag_pipeline.dataset import RagDataset, write_dataset


BASE_URL = "https://huggingface.co/datasets/Wix/WixQA/resolve/main"
CONFIGS = ("wixqa_expertwritten", "wixqa_simulated", "wixqa_synthetic")
URLS = {
    "corpus": f"{BASE_URL}/wix_kb_corpus/wix_kb_corpus.jsonl",
    **{config: f"{BASE_URL}/{config}/test.jsonl" for config in CONFIGS},
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(value)
    return rows


def _group(article_ids: Sequence[str]) -> str:
    return "wix-articles-" + hashlib.sha256(
        "\0".join(sorted(map(str, article_ids))).encode("utf-8")
    ).hexdigest()[:20]


def _split_for(group_id: str) -> str:
    # Same article set can never cross Dev/Heldout. The frozen ratio is 80/20.
    return "heldout" if int(hashlib.sha256(group_id.encode()).hexdigest()[:8], 16) % 5 == 0 else "dev"


def build_subset(
    corpus_rows: Sequence[Mapping[str, Any]],
    qa_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    output: Path,
    *,
    split: str,
    cases_per_config: int = 20,
    max_documents: int = 120,
) -> RagDataset:
    if split not in {"dev", "heldout"}:
        raise ValueError("split must be dev or heldout")
    if cases_per_config < 1 or max_documents < 1:
        raise ValueError("dataset limits must be positive")
    corpus = {
        str(row.get("id") or ""): row
        for row in corpus_rows if str(row.get("id") or "").strip()
    }
    cases: list[dict[str, Any]] = []
    selected_document_ids: set[str] = set()
    for config in CONFIGS:
        selected = 0
        for row_number, row in enumerate(qa_rows.get(config, ()), 1):
            article_ids = tuple(dict.fromkeys(
                str(value) for value in (row.get("article_ids") or ()) if str(value) in corpus
            ))
            if not article_ids:
                continue
            group_id = _group(article_ids)
            if _split_for(group_id) != split:
                continue
            new_ids = set(article_ids) - selected_document_ids
            if len(selected_document_ids) + len(new_ids) > max_documents:
                continue
            evidence = []
            for article_id in article_ids:
                content = str(corpus[article_id].get("contents") or "")
                if not content.strip():
                    break
                # WixQA supplies article-level grounding, not exhaustive character-span Gold.
                evidence.append({
                    "document_id": article_id,
                    "start_char": 0,
                    "end_char": len(content),
                    "quote": "",
                    "relevance": 3,
                })
            if len(evidence) != len(article_ids):
                continue
            question = str(row.get("question") or "").strip()
            answer = str(row.get("answer") or "").strip()
            if not question or not answer:
                continue
            selected_document_ids.update(article_ids)
            cases.append({
                "id": f"wixqa-{split}-{config}-{row_number}",
                "group_id": group_id,
                "split": split,
                "query": question,
                "history": [],
                "evidence": evidence,
                "query_types": [
                    "customer_support", config,
                    "multi_article" if len(article_ids) > 1 else "single_article",
                ],
                "required_claims": [answer],
                "forbidden_claims": [],
                "answerable": True,
            })
            selected += 1
            if selected >= cases_per_config:
                break

    documents = []
    for article_id in sorted(selected_document_ids):
        row = corpus[article_id]
        content = str(row.get("contents") or "")
        url = str(row.get("url") or "")
        title = str(row.get("title") or "").strip() or content.splitlines()[0][:200] or article_id
        documents.append({
            "id": article_id,
            "title": title,
            "content": content,
            "metadata": {
                "source": "WixQA",
                "url": url,
                "article_type": str(row.get("article_type") or "article"),
                "grounding_granularity": "article",
            },
        })
    if not cases:
        raise ValueError(f"no WixQA cases selected for split={split}")
    write_dataset(
        output,
        dataset_id=f"wixqa-rag-mini-{split}-v1",
        documents=documents,
        cases=cases,
        source={
            "dataset": "WixQA",
            "url": "https://huggingface.co/datasets/Wix/WixQA",
            "license": "MIT",
            "snapshot": "2024-12-02",
            "selection": "article-id-group-safe deterministic 80/20 split",
            "grounding_granularity": "article-level; not character-span Gold",
        },
    )
    return RagDataset.load(output)


def _download_all(root: Path) -> tuple[Path, dict[str, Path]]:
    root.mkdir(parents=True, exist_ok=True)
    corpus_path = root / "wix_kb_corpus.jsonl"
    urllib.request.urlretrieve(URLS["corpus"], corpus_path)
    qa_paths = {}
    for config in CONFIGS:
        path = root / f"{config}.jsonl"
        urllib.request.urlretrieve(URLS[config], path)
        qa_paths[config] = path
    return corpus_path, qa_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "heldout"), required=True)
    parser.add_argument("--cases-per-config", type=int, default=20)
    parser.add_argument("--max-documents", type=int, default=120)
    args = parser.parse_args()

    if args.input_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="dialogpilot-wixqa-")
        input_root = Path(temporary.name)
        corpus_path, qa_paths = _download_all(input_root)
    else:
        temporary = None
        corpus_path = args.input_dir / "wix_kb_corpus.jsonl"
        qa_paths = {config: args.input_dir / f"{config}.jsonl" for config in CONFIGS}
    try:
        dataset = build_subset(
            _read_jsonl(corpus_path),
            {config: _read_jsonl(path) for config, path in qa_paths.items()},
            args.output,
            split=args.split,
            cases_per_config=args.cases_per_config,
            max_documents=args.max_documents,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()
    print(json.dumps({
        "dataset_id": dataset.manifest["dataset_id"],
        "documents": len(dataset.documents),
        "cases": len(dataset.cases),
        "output": str(dataset.root),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
