"""Haystack-backed hierarchical retrieval adapters for the RAG evaluation.

Haystack owns hierarchical splitting and sibling auto-merging. DialogPilot owns
stable source projection, retrieval-score aggregation, and the final context
budget because those contracts are specific to this application.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from haystack import Document
from haystack.components.preprocessors import HierarchicalDocumentSplitter
from haystack.components.retrievers.auto_merging_retriever import AutoMergingRetriever
from haystack.document_stores.in_memory import InMemoryDocumentStore

from evaluation.rag_pipeline.contracts import RagDocument
from memory.context import TokenEstimator
from mcp.context_packer import ContextCandidate


# Haystack's word splitter is deterministic and does not require the optional
# NLTK data download used by sentence splitting. On the English support corpus,
# 192/384/768 words are the explicit proxies for approximately 256/512/1024
# estimated tokens. The report records both units instead of claiming equality.
HIERARCHY_WORD_BLOCKS = (768, 384, 192)
HIERARCHY_WORD_OVERLAP = 24
AUTO_MERGE_THRESHOLD = 0.5
RANK_SCORE_K = 10


@dataclass(frozen=True)
class HierarchyNode:
    node_id: str
    source_id: str
    title: str
    text: str
    start_char: int
    end_char: int
    level: int
    block_size_words: int
    parent_id: str | None
    children_ids: tuple[str, ...]
    document: Document


@dataclass
class HierarchyIndex:
    nodes: dict[str, HierarchyNode]
    leaf_ids: tuple[str, ...]
    source_documents: tuple[dict[str, str], ...]
    auto_merger: AutoMergingRetriever

    def ancestor_at_level(self, node_id: str, level: int) -> HierarchyNode:
        node = self.nodes[node_id]
        while node.level > level and node.parent_id in self.nodes:
            node = self.nodes[str(node.parent_id)]
        return node

    def top_parent(self, node_id: str) -> HierarchyNode:
        """Return the largest indexed node without crossing the source root."""
        node = self.nodes[node_id]
        while node.parent_id in self.nodes:
            node = self.nodes[str(node.parent_id)]
        return node

    def is_descendant(self, node_id: str, ancestor_id: str) -> bool:
        current = self.nodes[node_id]
        while True:
            if current.node_id == ancestor_id:
                return True
            if current.parent_id not in self.nodes:
                return False
            current = self.nodes[str(current.parent_id)]


def build_hierarchy(documents: Sequence[RagDocument]) -> HierarchyIndex:
    """Build a three-level tree and project every node to original coordinates."""
    splitter = HierarchicalDocumentSplitter(
        block_sizes=set(HIERARCHY_WORD_BLOCKS),
        split_overlap=HIERARCHY_WORD_OVERLAP,
        split_by="word",
    )
    nodes: dict[str, HierarchyNode] = {}
    leaf_ids: list[str] = []
    leaf_sources: list[dict[str, str]] = []
    store_documents: list[Document] = []

    for source in documents:
        root = Document(
            id=f"source::{source.document_id}",
            content=source.content,
            meta={
                "dialogpilot_source_id": source.document_id,
                "dialogpilot_title": source.title,
            },
        )
        split_documents = splitter.run(documents=[root])["documents"]
        raw_by_id = {document.id: document for document in split_documents}
        global_starts: dict[str, int] = {root.id: 0}

        def global_start(document: Document) -> int:
            cached = global_starts.get(document.id)
            if cached is not None:
                return cached
            parent_id = str(document.meta.get("__parent_id") or "")
            if parent_id not in raw_by_id:
                raise ValueError(f"hierarchy parent missing for node={document.id}")
            value = global_start(raw_by_id[parent_id]) + int(
                document.meta.get("split_idx_start") or 0
            )
            global_starts[document.id] = value
            return value

        for document in split_documents:
            level = int(document.meta.get("__level") or 0)
            children = tuple(map(str, document.meta.get("__children_ids") or ()))
            if level == 0 and children:
                # The source root is provenance, not a retrievable context. Capping
                # level-1 parents prevents auto-merge from returning the whole file.
                continue
            start = global_start(document)
            end = start + len(document.content or "")
            if source.content[start:end] != (document.content or ""):
                raise ValueError(
                    f"hierarchy source projection mismatch for node={document.id}"
                )
            parent_id = str(document.meta.get("__parent_id") or "") or None
            if level == 1:
                parent_id = None
            adapted_meta = dict(document.meta)
            adapted_meta["__parent_id"] = parent_id
            adapted = Document(
                id=document.id,
                content=document.content,
                meta=adapted_meta,
                score=document.score,
            )
            node = HierarchyNode(
                node_id=document.id,
                source_id=source.document_id,
                title=source.title,
                text=document.content or "",
                start_char=start,
                end_char=end,
                level=level,
                block_size_words=int(document.meta.get("__block_size") or 0),
                parent_id=parent_id,
                children_ids=children,
                document=adapted,
            )
            nodes[node.node_id] = node

        # A short source has no children and its root is itself the retrieval leaf.
        if root.id not in nodes:
            root_document = raw_by_id[root.id]
            if not root_document.meta.get("__children_ids"):
                nodes[root.id] = HierarchyNode(
                    node_id=root.id,
                    source_id=source.document_id,
                    title=source.title,
                    text=source.content,
                    start_char=0,
                    end_char=len(source.content),
                    level=0,
                    block_size_words=0,
                    parent_id=None,
                    children_ids=(),
                    document=root_document,
                )

    for node in nodes.values():
        if node.children_ids:
            store_documents.append(node.document)
        else:
            leaf_ids.append(node.node_id)
            leaf_sources.append({
                "id": node.node_id,
                "title": node.title,
                "content": node.text,
            })

    document_store = InMemoryDocumentStore()
    if store_documents:
        document_store.write_documents(store_documents)
    return HierarchyIndex(
        nodes=nodes,
        leaf_ids=tuple(leaf_ids),
        source_documents=tuple(leaf_sources),
        auto_merger=AutoMergingRetriever(
            document_store=document_store,
            threshold=AUTO_MERGE_THRESHOLD,
        ),
    )


def transform_leaf_hit(
    hit: Mapping[str, Any], hierarchy: HierarchyIndex,
) -> dict[str, Any]:
    """Project a retrieval hit inside a Haystack leaf to its source span."""
    leaf = hierarchy.nodes[str(hit["document_id"])]
    relative_start = int(hit.get("source_start_char") or 0)
    relative_end = int(hit.get("source_end_char") or len(leaf.text))
    return {
        **hit,
        "document_id": leaf.source_id,
        "source_start_char": leaf.start_char + relative_start,
        "source_end_char": leaf.start_char + relative_end,
        "hierarchy_node_id": leaf.node_id,
        "hierarchy_level": leaf.level,
        "title": leaf.title,
    }


def _ranked_leaf_scores(
    capture: Mapping[str, Any], *, limit: int,
) -> tuple[dict[str, float], dict[str, int]]:
    scores: dict[str, float] = {}
    best_ranks: dict[str, int] = {}
    for rank, chunk_id in enumerate(capture["reranked_ids"][:limit], 1):
        node_id = str(capture["hit_by_id"][chunk_id]["hierarchy_node_id"])
        scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (RANK_SCORE_K + rank)
        best_ranks[node_id] = min(rank, best_ranks.get(node_id, rank))
    return scores, best_ranks


def _covered_leaf_ids(
    hierarchy: HierarchyIndex,
    node_id: str,
    ranked_leaf_ids: Iterable[str],
) -> frozenset[str]:
    return frozenset(
        leaf_id for leaf_id in ranked_leaf_ids
        if hierarchy.is_descendant(leaf_id, node_id)
    )


def _context(
    node: HierarchyNode,
    *,
    score: float,
    best_rank: int,
    prefix: str,
) -> ContextCandidate:
    return ContextCandidate(
        chunk_id=f"{prefix}::{node.node_id}",
        document_id=node.source_id,
        text=node.text,
        start_char=node.start_char,
        end_char=node.end_char,
        title=node.title,
        score=score,
        ranks=(("rerank", best_rank),),
    )


def unique_parent_candidates(
    capture: Mapping[str, Any],
    hierarchy: HierarchyIndex,
    *,
    candidate_k: int,
    final_k: int,
) -> tuple[ContextCandidate, ...]:
    """Aggregate all child Top-K ranks, then deduplicate parents before Top-K."""
    leaf_scores, leaf_ranks = _ranked_leaf_scores(capture, limit=candidate_k)
    grouped: dict[str, list[str]] = {}
    for leaf_id in leaf_scores:
        parent = hierarchy.top_parent(leaf_id)
        grouped.setdefault(parent.node_id, []).append(leaf_id)
    ranked_groups = []
    for parent_id, leaf_ids in grouped.items():
        aggregate = sum(leaf_scores[leaf_id] for leaf_id in leaf_ids)
        best_rank = min(leaf_ranks[leaf_id] for leaf_id in leaf_ids)
        # sum(reciprocal ranks) is exactly max child contribution plus sibling
        # support, without introducing another uncalibrated coefficient.
        ranked_groups.append((aggregate, best_rank, parent_id))
    ranked_groups.sort(key=lambda item: (-item[0], item[1], item[2]))
    return tuple(
        _context(
            hierarchy.nodes[parent_id],
            score=aggregate,
            best_rank=best_rank,
            prefix="unique-parent",
        )
        for aggregate, best_rank, parent_id in ranked_groups[:final_k]
    )


def auto_merged_nodes(
    capture: Mapping[str, Any],
    hierarchy: HierarchyIndex,
    *,
    candidate_k: int,
) -> tuple[tuple[HierarchyNode, float, int, frozenset[str]], ...]:
    """Run Haystack auto-merge and attach DialogPilot's aggregate rank evidence."""
    leaf_scores, leaf_ranks = _ranked_leaf_scores(capture, limit=candidate_k)
    retrieved = [
        replace(hierarchy.nodes[leaf_id].document, score=score)
        for leaf_id, score in leaf_scores.items()
    ]
    mergeable = [document for document in retrieved if document.meta.get("__parent_id")]
    # A splitter remainder can already be a capped level-1 leaf. It has no legal
    # parent to merge into, so preserve it alongside the official merger output.
    terminal = [document for document in retrieved if not document.meta.get("__parent_id")]
    merged = (
        hierarchy.auto_merger.run(documents=mergeable)["documents"]
        if mergeable else []
    ) + terminal
    values = []
    for document in merged:
        node = hierarchy.nodes[document.id]
        covered = _covered_leaf_ids(hierarchy, node.node_id, leaf_scores)
        aggregate = sum(leaf_scores[leaf_id] for leaf_id in covered)
        best_rank = min(leaf_ranks[leaf_id] for leaf_id in covered)
        values.append((node, aggregate, best_rank, covered))
    values.sort(key=lambda item: (-item[1], item[2], item[0].node_id))
    return tuple(values)


def dynamic_auto_merge_candidates(
    capture: Mapping[str, Any],
    hierarchy: HierarchyIndex,
    *,
    candidate_k: int,
) -> tuple[ContextCandidate, ...]:
    return tuple(
        _context(node, score=score, best_rank=rank, prefix="auto-merge")
        for node, score, rank, _covered in auto_merged_nodes(
            capture, hierarchy, candidate_k=candidate_k,
        )
    )


@dataclass(frozen=True)
class _BudgetOption:
    node: HierarchyNode
    covered: frozenset[str]
    score: float
    best_rank: int
    estimated_tokens: int


def budget_aware_mixed_candidates(
    capture: Mapping[str, Any],
    hierarchy: HierarchyIndex,
    *,
    candidate_k: int,
    max_tokens: int,
    max_chunks: int,
) -> tuple[ContextCandidate, ...]:
    """Pack Haystack's mixed granularity with deterministic budget fallback.

    Gold spans are deliberately absent from this API. Marginal reciprocal-rank
    evidence is primary and token cost is a feasibility/tie-break constraint.
    When a preferred merged node cannot fit, it is replaced by its relevant
    children and reconsidered.
    """
    leaf_scores, leaf_ranks = _ranked_leaf_scores(capture, limit=candidate_k)
    estimator = TokenEstimator()

    def option(node: HierarchyNode, covered: frozenset[str]) -> _BudgetOption:
        return _BudgetOption(
            node=node,
            covered=covered,
            score=sum(leaf_scores[leaf_id] for leaf_id in covered),
            best_rank=min(leaf_ranks[leaf_id] for leaf_id in covered),
            estimated_tokens=estimator.estimate(node.text),
        )

    preferred: dict[str, frozenset[str]] = {}
    for node, _score, _rank, covered in auto_merged_nodes(
        capture, hierarchy, candidate_k=candidate_k,
    ):
        preferred[node.node_id] = preferred.get(node.node_id, frozenset()) | covered
    queue = [option(hierarchy.nodes[node_id], covered) for node_id, covered in preferred.items()]
    selected: list[ContextCandidate] = []
    selected_nodes: list[HierarchyNode] = []
    selected_spans: set[tuple[str, int, int]] = set()
    covered_leaves: set[str] = set()
    used_tokens = 0

    while queue and len(selected) < max_chunks:
        # Relevance is the primary objective; tokens are a feasibility constraint
        # and deterministic tie-break. Pure score/token starves the five context
        # slots with small but weak leaves on this support corpus.
        queue.sort(key=lambda item: (
            -sum(leaf_scores[leaf] for leaf in item.covered - covered_leaves),
            item.estimated_tokens,
            item.best_rank,
            item.node.node_id,
        ))
        current = queue.pop(0)
        marginal = current.covered - covered_leaves
        if not marginal:
            continue
        identity = (
            current.node.source_id,
            current.node.start_char,
            current.node.end_char,
        )
        if (
            identity not in selected_spans
            and used_tokens + current.estimated_tokens <= max_tokens
        ):
            selected.append(_context(
                current.node,
                score=current.score,
                best_rank=current.best_rank,
                prefix="mixed",
            ))
            selected_nodes.append(current.node)
            selected_spans.add(identity)
            covered_leaves.update(current.covered)
            used_tokens += current.estimated_tokens
            continue

        # A parent is an indivisible context, but not an indivisible decision:
        # descend to relevant windows/leaves when it cannot fit the remainder.
        for child_id in current.node.children_ids:
            child = hierarchy.nodes.get(child_id)
            if child is None:
                continue
            child_covered = _covered_leaf_ids(hierarchy, child_id, marginal)
            if child_covered:
                queue.append(option(child, child_covered))

    # Spend only the remaining budget on local context. This cannot evict a
    # selected anchor: an isolated 256-level leaf upgrades to its 512-level
    # ancestor only when the window fits and does not swallow another context.
    for index, node in enumerate(tuple(selected_nodes)):
        if node.children_ids or node.level < 3:
            continue
        window = hierarchy.ancestor_at_level(node.node_id, 2)
        if window.node_id == node.node_id:
            continue
        overlaps_another = any(
            other_index != index
            and other.source_id == window.source_id
            and other.start_char < window.end_char
            and other.end_char > window.start_char
            for other_index, other in enumerate(selected_nodes)
        )
        window_tokens = estimator.estimate(window.text)
        leaf_tokens = estimator.estimate(node.text)
        if overlaps_another or used_tokens - leaf_tokens + window_tokens > max_tokens:
            continue
        original = selected[index]
        selected[index] = _context(
            window,
            score=original.score,
            best_rank=original.ranks[0][1],
            prefix="mixed-window",
        )
        selected_nodes[index] = window
        used_tokens = used_tokens - leaf_tokens + window_tokens

    return tuple(selected)


def hierarchy_contract() -> dict[str, Any]:
    return {
        "splitter": "haystack.HierarchicalDocumentSplitter",
        "auto_merger": "haystack.AutoMergingRetriever",
        "split_by": "word",
        "block_sizes_words": list(HIERARCHY_WORD_BLOCKS),
        "split_overlap_words": HIERARCHY_WORD_OVERLAP,
        "target_estimated_token_levels": [1024, 512, 256],
        "auto_merge_threshold": AUTO_MERGE_THRESHOLD,
        "rank_aggregation": "sum(1/(10+child_rerank)); max child + sibling support",
        "mixed_packing_utility": (
            "lexicographic marginal retrieved-leaf reciprocal-rank evidence, "
            "then token cost; rejected parents descend"
        ),
        "gold_used_by_selection": False,
    }
