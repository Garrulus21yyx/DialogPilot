from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from evaluation.rag_ecommerce_dev import synthetic_development
from evaluation.rag_provider_free import projection, replay, select_dev, summarize


def test_dev_selection_preserves_group_boundary_and_is_order_independent():
    _, cases = synthetic_development()
    heldout = replace(cases[0], case_id="heldout", group_id="heldout", split="heldout")
    rows = cases + (heldout,)
    selected = select_dev(SimpleNamespace(cases=rows), 100)
    assert all(c.split == "dev" for c in selected)
    assert len({c.group_id for c in selected}) == len(selected)
    assert selected == select_dev(SimpleNamespace(cases=tuple(reversed(rows))), 100)


@pytest.mark.parametrize("strategy", ["fixed_tokens", "structure_aware"])
def test_wire_roundtrip_preserves_source_evidence_and_stage_monotonicity(strategy):
    docs, cases = synthetic_development()
    _, chunks, texts = projection(docs, cases, 512, 64, strategy)
    scores = np.ones((len(cases), len(chunks)))
    rows = replay(docs, cases, chunks, texts, [c.query for c in cases], scores, scores)
    for row in rows:
        m = row["metrics"]
        assert (
            m["tool_message_complete"]
            <= m["packed_complete"]
            <= m["selected_complete"]
            <= m["candidate_complete"]
            <= m["chunk_complete"]
        )
        for item in row["tool_message"]["evidence"]:
            ref = item["source"]
            doc = next(d for d in docs if d.document_id == ref["source_id"])
            assert item["text"] == doc.content[ref["start_char"] : ref["end_char"]]
    assert all(s["delta_pp"] == 0 for s in summarize(rows) if s["dense_weight"] == 0.25)


def test_adaptive_selection_never_uses_test_fold_labels():
    from copy import deepcopy
    from evaluation.rag_fusion_selection import evaluate_selection
    from evaluation.rag_provider_free import WEIGHTS

    rows = []
    for i in range(30):
        for w in WEIGHTS:
            rows.append(
                {
                    "case_id": str(i),
                    "group_id": str(i // 2),
                    "corpus_type": "public_doc2dial",
                    "query": "question",
                    "dense_weight": w,
                    "metrics": {"tool_message_complete": w == 0.5},
                }
            )
    result = evaluate_selection(rows)
    fold0 = {r["case_id"] for r in result["cases"] if r["fold"] == 0}
    changed = deepcopy(rows)
    for row in changed:
        if row["case_id"] in fold0:
            row["metrics"]["tool_message_complete"] = row["dense_weight"] == 1
    again = evaluate_selection(changed)
    before = {r["case_id"]: r["weights"] for r in result["cases"] if r["fold"] == 0}
    after = {r["case_id"]: r["weights"] for r in again["cases"] if r["fold"] == 0}
    assert before == after
