from inspect import signature

from evaluation.rag_hierarchical_adapter import (
    budget_aware_mixed_candidates,
    build_hierarchy,
    dynamic_auto_merge_candidates,
    unique_parent_candidates,
)
from evaluation.rag_pipeline.contracts import RagDocument
from memory.context import TokenEstimator


def _hierarchy(word_count: int = 2400):
    text = " ".join(f"policy{i}" for i in range(word_count))
    source = RagDocument("policy", "Policy", text)
    return source, build_hierarchy((source,))


def _capture(node_ids):
    chunk_ids = [f"hit-{index}" for index in range(len(node_ids))]
    return {
        "reranked_ids": chunk_ids,
        "hit_by_id": {
            chunk_id: {"hierarchy_node_id": node_id}
            for chunk_id, node_id in zip(chunk_ids, node_ids, strict=True)
        },
    }


def test_hierarchy_nodes_keep_exact_global_source_coordinates():
    source, hierarchy = _hierarchy()

    assert len(hierarchy.leaf_ids) > 3
    assert all(
        source.content[node.start_char:node.end_char] == node.text
        for node in hierarchy.nodes.values()
    )
    # Repeated content positions are not guessed with str.find; nested offsets
    # are recursively projected from Haystack's parent-relative coordinates.
    assert any(node.level >= 3 and node.start_char > 0 for node in hierarchy.nodes.values())


def test_unique_parent_aggregates_before_parent_top_k():
    _source, hierarchy = _hierarchy()
    groups = {}
    for leaf_id in hierarchy.leaf_ids:
        groups.setdefault(hierarchy.top_parent(leaf_id).node_id, []).append(leaf_id)
    populated = [values for values in groups.values() if len(values) >= 2]
    assert len(populated) >= 2
    ranked_leaves = populated[0][:2] + populated[1][:2]

    contexts = unique_parent_candidates(
        _capture(ranked_leaves), hierarchy, candidate_k=20, final_k=5,
    )

    assert len(contexts) == 2
    assert len({context.chunk_id for context in contexts}) == 2
    expected_first_score = 1 / 11 + 1 / 12
    assert abs(contexts[0].score - expected_first_score) < 1e-12


def test_dynamic_auto_merge_promotes_majority_siblings():
    _source, hierarchy = _hierarchy()
    parent = next(
        node for node in hierarchy.nodes.values()
        if len([child for child in node.children_ids if child in hierarchy.leaf_ids]) >= 3
    )
    leaves = [child for child in parent.children_ids if child in hierarchy.leaf_ids][:2]

    contexts = dynamic_auto_merge_candidates(
        _capture(leaves), hierarchy, candidate_k=20,
    )

    assert any(context.chunk_id.endswith(parent.node_id) for context in contexts)


def test_budget_aware_mixed_descends_when_promoted_parent_does_not_fit():
    _source, hierarchy = _hierarchy()
    top_parent = next(
        node for node in hierarchy.nodes.values()
        if node.level == 1 and node.children_ids
    )
    leaves = [
        leaf_id for leaf_id in hierarchy.leaf_ids
        if hierarchy.is_descendant(leaf_id, top_parent.node_id)
    ]
    capture = _capture(leaves)
    parent_tokens = TokenEstimator().estimate(top_parent.text)

    contexts = budget_aware_mixed_candidates(
        capture,
        hierarchy,
        candidate_k=20,
        max_tokens=max(1, parent_tokens - 1),
        max_chunks=5,
    )

    assert contexts
    assert all(not context.chunk_id.endswith(top_parent.node_id) for context in contexts)
    assert TokenEstimator().estimate("\n\n".join(c.text for c in contexts)) <= parent_tokens - 1


def test_budget_selection_contract_cannot_receive_gold_labels_and_is_deterministic():
    _source, hierarchy = _hierarchy()
    capture = _capture(hierarchy.leaf_ids[:8])

    assert "case" not in signature(budget_aware_mixed_candidates).parameters
    first = budget_aware_mixed_candidates(
        capture, hierarchy, candidate_k=20, max_tokens=2600, max_chunks=5,
    )
    second = budget_aware_mixed_candidates(
        capture, hierarchy, candidate_k=20, max_tokens=2600, max_chunks=5,
    )
    assert [context.chunk_id for context in first] == [context.chunk_id for context in second]
