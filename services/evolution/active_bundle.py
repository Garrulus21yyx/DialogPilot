"""Single active AgentBundle selection for the local runtime."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Mapping

from .bundle import AgentBundle
from .registry import AgentBundleRegistry


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


@dataclass(frozen=True)
class ActiveBundleAssignment:
    primary: AgentBundle
    pinned_refs: PinnedExecutionRefs


class ActiveBundleResolver:
    """Resolve every request to the repository's single configured active Bundle."""

    def __init__(
        self,
        registry: AgentBundleRegistry,
        *,
        execution_ref_resolver: Callable[[AgentBundle], Mapping[str, str]] | None = None,
    ) -> None:
        self._registry = registry
        self._execution_ref_resolver = execution_ref_resolver

    def resolve(self, _subject_key: str = "") -> ActiveBundleAssignment:
        bundle = self._registry.active()
        values = (
            dict(self._execution_ref_resolver(bundle))
            if self._execution_ref_resolver is not None else {
                "route_policy_ref": (
                    f"local-route:{bundle.component_hash('routing_policy')}"
                ),
                "knowledge_backend_ref": "local-knowledge",
                "knowledge_generation_ref": "local-active-generation",
                "corpus_manifest_ref": "local-corpus",
                "retrieval_policy_ref": bundle.component_hash("retrieval_policy"),
            }
        )
        refs = PinnedExecutionRefs(
            bundle_version=bundle.version,
            bundle_hash=bundle.content_hash,
            route_policy_ref=str(values.get("route_policy_ref") or ""),
            knowledge_backend_ref=str(values.get("knowledge_backend_ref") or ""),
            knowledge_generation_ref=str(values.get("knowledge_generation_ref") or ""),
            corpus_manifest_ref=str(values.get("corpus_manifest_ref") or ""),
            retrieval_policy_ref=str(values.get("retrieval_policy_ref") or ""),
        )
        return ActiveBundleAssignment(bundle, refs)
