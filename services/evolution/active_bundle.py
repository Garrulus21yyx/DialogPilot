"""Pinned knowledge execution references; runtime selection belongs to Target."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass



@dataclass(frozen=True)
class PinnedExecutionRefs:
    bundle_version: str
    bundle_hash: str
    route_policy_ref: str
    knowledge_backend_ref: str
    knowledge_generation_ref: str
    corpus_manifest_ref: str
    retrieval_policy_ref: str

    def __post_init__(self) -> None:
        if any(not str(value).strip() for value in self.__dict__.values()):
            raise ValueError("pinned execution refs are incomplete")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(
            self.__dict__, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
