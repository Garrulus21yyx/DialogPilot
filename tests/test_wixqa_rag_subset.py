"""WixQA 客服 mini suite 的 article-group split 合同。"""

from scripts.build_wixqa_rag_subset import CONFIGS, build_subset


def test_wixqa_builder_keeps_article_groups_out_of_both_splits(tmp_path):
    corpus = [{
        "id": f"article-{index}",
        "url": f"https://support.example/{index}",
        "contents": f"Policy article {index}: follow the documented support steps.",
        "article_type": "article",
    } for index in range(60)]
    qa = {
        config: [{
            "question": f"How do I resolve issue {index}?",
            "answer": f"Follow policy article {index}.",
            "article_ids": [f"article-{index}"],
        } for index in range(60)]
        for config in CONFIGS
    }

    dev = build_subset(
        corpus, qa, tmp_path / "dev", split="dev",
        cases_per_config=5, max_documents=30,
    )
    heldout = build_subset(
        corpus, qa, tmp_path / "heldout", split="heldout",
        cases_per_config=5, max_documents=30,
    )

    assert {case.group_id for case in dev.cases}.isdisjoint(
        {case.group_id for case in heldout.cases}
    )
    assert all(case.evidence for case in dev.cases + heldout.cases)
    assert all(
        evidence.start_char == 0
        and evidence.end_char > 0
        and evidence.granularity == "document"
        for case in dev.cases + heldout.cases for evidence in case.evidence
    )
    assert dev.manifest["source"]["grounding_granularity"].startswith("article-level")


def test_wixqa_builder_can_select_a_long_document_pressure_slice(tmp_path):
    corpus = [{
        "id": f"article-{index}",
        "contents": ("long policy step " * 700) if index % 2 else "short policy",
    } for index in range(80)]
    qa = {
        config: [{
            "question": f"How do I resolve issue {index}?",
            "answer": f"Follow policy article {index}.",
            "article_ids": [f"article-{index}"],
        } for index in range(80)]
        for config in CONFIGS
    }

    dataset = build_subset(
        corpus, qa, tmp_path / "long", split="dev",
        cases_per_config=3, max_documents=20,
        min_relevant_document_chars=8_000,
    )

    assert dataset.manifest["dataset_id"] == "wixqa-rag-long8000-dev-v1"
    assert all("long_document" in case.query_types for case in dataset.cases)
    assert all(len(document.content) >= 8_000 for document in dataset.documents)
