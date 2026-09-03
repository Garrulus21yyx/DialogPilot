#!/usr/bin/env python3
"""Freeze the balanced Doc2Dial English heldout dataset."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.doc2dial_heldout_dataset import (  # noqa: E402
    DOMAIN_BALANCED_SUPPLEMENT_QUOTAS,
    DOC2DIAL_URL,
    freeze_doc2dial_heldout,
)
from evaluation.rag_pipeline.dataset import RagDataset  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--excluded-dataset", required=True, action="append", type=Path,
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--dataset-id", default="doc2dial-rag-en-heldout-balanced-v1",
    )
    parser.add_argument(
        "--selection-seed", default="doc2dial-en-heldout-balanced-v1",
    )
    parser.add_argument("--per-stratum", type=int, default=10)
    parser.add_argument(
        "--domain-balanced-supplement", action="store_true",
        help="Select the fixed 120-case, 30-per-domain residual-test profile",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> RagDataset:
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("heldout dataset output must be empty")
    excluded = tuple(
        RagDataset.load(path, verify_checksum=True)
        for path in args.excluded_dataset
    )
    quotas = (
        DOMAIN_BALANCED_SUPPLEMENT_QUOTAS
        if args.domain_balanced_supplement else None
    )
    if args.archive is not None:
        return freeze_doc2dial_heldout(
            args.archive, args.output, excluded_dataset=excluded,
            dataset_id=args.dataset_id, selection_seed=args.selection_seed,
            per_stratum=args.per_stratum,
            stratum_quotas=quotas,
        )
    with tempfile.TemporaryDirectory(prefix="dialogpilot-doc2dial-heldout-") as temp:
        archive = Path(temp) / "doc2dial_v1.0.1.zip"
        urllib.request.urlretrieve(DOC2DIAL_URL, archive)
        return freeze_doc2dial_heldout(
            archive, args.output, excluded_dataset=excluded,
            dataset_id=args.dataset_id, selection_seed=args.selection_seed,
            per_stratum=args.per_stratum,
            stratum_quotas=quotas,
        )


def main(argv: list[str] | None = None) -> int:
    try:
        dataset = run(parse_args(argv))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "dataset_id": dataset.manifest["dataset_id"],
                "documents": len(dataset.documents),
                "cases": len(dataset.cases),
                "corpus_sha256": dataset.manifest["corpus_sha256"],
                "cases_sha256": dataset.manifest["cases_sha256"],
                "output": str(dataset.root),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
