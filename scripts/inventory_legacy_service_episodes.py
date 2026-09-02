#!/usr/bin/env python3
"""Read-only legacy Chroma inventory; emits no raw conversation content."""
from __future__ import annotations

import argparse
import json

import chromadb

from application.service_episode_backfill import (
    ServiceEpisodeBackfillPolicy,
    legacy_record,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--collection", default="episodic")
    args = parser.parse_args()
    collection = chromadb.HttpClient(
        host=args.host, port=args.port,
    ).get_collection(args.collection)
    payload = collection.get(include=["documents", "metadatas"])
    ids = list(payload.get("ids") or [])
    documents = list(payload.get("documents") or [])
    metadatas = list(payload.get("metadatas") or [])
    if not (len(ids) == len(documents) == len(metadatas) == collection.count()):
        raise RuntimeError("legacy Chroma inventory shape/count mismatch")
    records = tuple(
        legacy_record(str(item_id), str(document), dict(metadata or {}))
        for item_id, document, metadata in zip(
            ids, documents, metadatas, strict=True,
        )
    )
    report = ServiceEpisodeBackfillPolicy().inventory(
        records,
        source_watermark=f"chroma:{args.collection}:count:{len(records)}",
    ).to_dict()
    report["source"] = {
        "backend": "LEGACY_CHROMA",
        "collection": args.collection,
        "raw_content_exported": False,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
