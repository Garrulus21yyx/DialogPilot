import json
import asyncio
import zipfile
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from evaluation.rag_pipeline import (
    EvidenceSpan,
    QueryVariant,
    RagCase,
    RagDocument,
    StageTrace,
    evaluate_chunk_projection,
    evaluate_query_variants,
    evaluate_stage_trace,
    fuse_rankings,
    select_configuration,
)
from evaluation.rag_pipeline.metrics import (
    chunk_contains_evidence,
    evaluate_ranked_hits,
    project_chunks,
)
from mcp.document_chunker import ChunkStrategy
from mcp.query_transformer import QueryTransformer
from mcp.result_reranker import (
    RerankCandidate,
    ResultReranker,
    _RerankDeps,
    _StructuredRerankOutput,
)
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.grounded_answer_generator import (
    GroundedAnswerGenerator,
    _GroundingDeps,
    _StructuredConflict,
    _StructuredGroundedOutput,
    _StructuredSegment,
)
from evaluation.rag_generation_evaluation import language_matches, token_f1
from core.model_policy import ModelProfile
from evaluation.rag_pipeline.selection import paired_group_bootstrap_delta
from evaluation.rag_pipeline.query_metrics import extract_negations
from scripts.build_doc2dial_rag_subset import build_subset
from evaluation.retrieval_eval_utils import query_variants, query_weights
from evaluation.rag_query_capture import capture_raw_queries


def test_overlap_is_credited_only_when_it_preserves_authoritative_evidence():
    document = RagDocument(
        document_id="refund-policy",
        title="退款政策",
        content="背景规则。" * 40 + "审核通过后款项原路退回。" + "其他规则。" * 40,
    )
    baseline = project_chunks(
        [document], max_tokens=24, overlap_tokens=0,
        strategy=ChunkStrategy.FIXED_TOKENS,
    )
    boundary = baseline[0].end_char
    evidence = EvidenceSpan("refund-policy", boundary - 3, boundary + 3)
    case = RagCase(
        case_id="refund-boundary",
        group_id="refund-boundary",
        split="dev",
        query="退款规则是什么",
        evidence=(evidence,),
    )

    without_overlap, _ = evaluate_chunk_projection(
        [document], [case], max_tokens=24, overlap_tokens=0,
        strategy=ChunkStrategy.FIXED_TOKENS,
    )
    with_overlap, chunks = evaluate_chunk_projection(
        [document], [case], max_tokens=24, overlap_tokens=6,
        strategy=ChunkStrategy.FIXED_TOKENS,
    )

    assert without_overlap["evidence_containment_rate"] == 0.0
    assert without_overlap["boundary_fragmentation_rate"] == 1.0
    assert with_overlap["evidence_containment_rate"] == 1.0
    assert with_overlap["duplicate_char_ratio"] > 0
    assert all(chunk.content == document.content[chunk.start_char:chunk.end_char] for chunk in chunks)


def test_chunk_offsets_stay_in_original_coordinates_with_leading_whitespace():
    content = "\n\n  Heading\nThe authoritative evidence starts here."
    document = RagDocument("doc", "Heading", content)
    chunks = project_chunks(
        [document], max_tokens=8, overlap_tokens=2,
        strategy=ChunkStrategy.STRUCTURE_AWARE,
    )
    evidence_start = content.index("authoritative")
    evidence = EvidenceSpan("doc", evidence_start, evidence_start + len("authoritative"))
    assert all(chunk.content == content[chunk.start_char:chunk.end_char] for chunk in chunks)
    assert any(
        chunk.start_char <= evidence.start_char and chunk.end_char >= evidence.end_char
        for chunk in chunks
    )


def test_weighted_rrf_is_replayable_and_weight_scale_invariant():
    rankings = {
        "raw:bm25": ["exact", "semantic", "noise"],
        "raw:vector": ["semantic", "noise", "exact"],
    }
    first = fuse_rankings(
        rankings,
        weights={"raw:bm25": 0.75, "raw:vector": 0.25},
        rrf_k=30,
        top_k=3,
    )
    scaled = fuse_rankings(
        rankings,
        weights={"raw:bm25": 3.0, "raw:vector": 1.0},
        rrf_k=30,
        top_k=3,
    )
    assert first == scaled
    assert first[0] == "exact"


def test_query_variant_metrics_expose_entity_negation_and_hallucination_risk():
    metrics = evaluate_query_variants(
        "订单 A123 不是退款，查询 E401",
        (
            QueryVariant("raw", "订单 A123 不是退款，查询 E401"),
            QueryVariant("rewrite", "查询订单 A123 的 E401 登录问题"),
            QueryVariant("expansion", "查询订单 A999 的 E401 退款问题"),
        ),
    )
    assert metrics["raw_query_retained"] == 1.0
    assert metrics["entity_preservation"] < 1.0
    assert metrics["negation_preservation"] < 1.0
    assert metrics["hallucinated_entity_rate"] > 0.0
    assert metrics["variant_token_diversity"] > 0.0


def test_english_negation_metric_uses_word_boundaries():
    assert extract_negations("I want to know whether it is available") == set()
    assert extract_negations("No, I don't want it without approval") == {
        "no", "don't", "without",
    }


def test_query_transformer_retains_raw_and_marks_generated_text_non_evidence():
    responses = iter((
        '{"query":"订单 A123 不能退款的处理条件"}',
        '{"queries":["订单 A123 不能退款政策", "订单 A123 退款失败处理"]}',
        '{"passage":"帮助中心通常会说明退款资格与失败处理步骤。"}',
    ))

    class FakeMessages:
        async def create(self, **_kwargs):
            text = next(responses)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=text)],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

    client = SimpleNamespace(messages=FakeMessages())
    result = asyncio.run(QueryTransformer(client, ModelProfile("test-model")).transform(
        "它不能退款吗，订单 A123？",
        history=("用户：订单 A123 显示退款失败",),
    ))
    variants = result.multi_query()
    assert variants[0].kind == "raw"
    assert variants[0].text == "它不能退款吗，订单 A123？"
    assert all(not item.usable_as_evidence for item in variants)
    assert result.standalone == "订单 A123 不能退款的处理条件"
    assert len(result.expansions) == 2
    assert result.hyde
    assert result.errors == ()


def test_query_transformer_fails_closed_to_raw_query():
    class BrokenMessages:
        async def create(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    transformer = QueryTransformer(
        SimpleNamespace(messages=BrokenMessages()), ModelProfile("test-model"),
    )
    result = asyncio.run(transformer.transform("退款流程", history=("之前的问题",)))
    assert result.raw_query == "退款流程"
    assert result.standalone == "退款流程"
    assert result.expansions == ()
    assert result.hyde == ""
    assert {item.split(":", 1)[0] for item in result.errors} == {
        "standalone", "multi_query", "hyde",
    }


def test_query_ablation_weights_reserve_raw_mass_and_keep_hyde_vector_only():
    row = {
        "raw_query": "refund A123",
        "standalone": "refund order A123",
        "expansions": ["refund eligibility A123", "refund failure A123"],
        "hyde": "A help article about refund eligibility",
    }
    variants = query_variants(row, "all")
    weights = query_weights(
        variants, raw_mass=0.5, lexical_weight=0.75, vector_weight=0.25,
    )
    assert sum(weights.values()) == 1.0
    assert weights["raw:bm25"] + weights["raw:vector"] == 0.5
    assert "hyde:bm25" not in weights
    assert weights["hyde:vector"] > 0


def test_reranker_rejects_any_non_exact_candidate_permutation():
    class FakeAgent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(
                output=_StructuredRerankOutput(
                    ordered_ids=["R02", "invented", "R02"],
                ),
            )

    reranker = ResultReranker(
        object(), ModelProfile("test-model"), structured_agent=FakeAgent(),
    )
    result = asyncio.run(reranker.rerank("refund", (
        RerankCandidate("c1", "general information"),
        RerankCandidate("c2", "refund policy"),
        RerankCandidate("c3", "contact information"),
    )))
    assert result.model_ordered_ids == ()
    assert result.ordered_ids == ("c1", "c2", "c3")
    assert result.error == "ValueError"

    with pytest.raises(ModelRetry):
        ResultReranker._validate_output(
            SimpleNamespace(deps=_RerankDeps(("R01", "R02", "R03"))),
            _StructuredRerankOutput(ordered_ids=["R02", "invented", "R02"]),
        )


def test_reranker_accepts_typed_bare_id_array_from_provider():
    class FakeAgent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(
                output=_StructuredRerankOutput(ordered_ids=["R02", "R01"]),
            )

    result = asyncio.run(ResultReranker(
        object(), ModelProfile("test-model"), structured_agent=FakeAgent(),
    ).rerank("refund", (
        RerankCandidate("c1", "general"), RerankCandidate("c2", "refund policy"),
    )))
    assert result.error is None
    assert result.ordered_ids == ("c2", "c1")


def test_reranker_output_budget_has_room_for_twenty_short_aliases():
    reranker = ResultReranker(
        object(), ModelProfile("test-model"), structured_agent=object(),
    )
    aliases = tuple(f"R{index:02d}" for index in range(1, 21))

    settings = reranker._model_settings(aliases)

    assert settings["max_tokens"] == 768


def test_context_packer_respects_budget_and_provenance_deduplication():
    candidates = (
        ContextCandidate("c1", "doc", "alpha " * 20, 0, 120),
        ContextCandidate("c2", "doc", "beta " * 20, 20, 130),
        ContextCandidate("c3", "other", "gamma " * 20, 0, 120),
    )
    packed = ContextPacker().pack(
        candidates, max_tokens=100, max_chunks=3, redundancy_threshold=0.5,
    )
    assert packed.chunk_ids == ("c1", "c3")
    assert packed.skipped_redundant == ("c2",)
    assert packed.token_count <= 100


def test_grounded_generator_rejects_unknown_evidence_and_fails_closed():
    invalid = _StructuredGroundedOutput(
        status="answered",
        segments=[_StructuredSegment(text="退款需要审核。", evidence_ids=["unknown"])],
        conflicts=[],
        reason="",
    )
    with pytest.raises(ModelRetry):
        GroundedAnswerGenerator._validate_output(
            SimpleNamespace(deps=_GroundingDeps(frozenset({"E1"}))),
            invalid,
        )

    class FailingStructuredAgent:
        async def run(self, *_args, **_kwargs):
            raise RuntimeError("output retries exhausted")

    generator = GroundedAnswerGenerator(
        None,
        ModelProfile("test-model"),
        structured_agent=FailingStructuredAgent(),
    )
    answer = asyncio.run(generator.generate("退款", (
        ContextCandidate("c1", "doc", "退款需要审核。", 0, 7),
    )))
    assert answer.abstained is True
    assert answer.citations == ()
    assert answer.reason == "generation_contract_error"
    assert answer.error == "RuntimeError"


def test_grounded_generator_derives_answer_claims_and_citations_from_segments():
    output = _StructuredGroundedOutput(
        status="answered",
        segments=[
            _StructuredSegment(text="退款需要审核。", evidence_ids=["E1"]),
            _StructuredSegment(text="审核通过后原路退回。", evidence_ids=["E1", "E2"]),
        ],
        conflicts=[],
        reason="",
    )

    class FakeStructuredAgent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(output=output)

    answer = asyncio.run(GroundedAnswerGenerator(
        None,
        ModelProfile("test-model"),
        structured_agent=FakeStructuredAgent(),
    ).generate("退款怎么到账？", (
        ContextCandidate("c1", "refund", "退款需要审核。", 0, 7),
        ContextCandidate("c2", "payment", "审核通过后原路退回。", 0, 11),
    )))

    assert answer.answer == "退款需要审核。\n审核通过后原路退回。"
    assert answer.citations == ("c1", "c2")
    assert tuple(claim.text for claim in answer.claims) == (
        "退款需要审核。",
        "审核通过后原路退回。",
    )
    assert answer.claims[1].citations == ("c1", "c2")
    assert answer.abstained is False


def test_grounded_generator_returns_claim_citations_and_typed_conflicts():
    output = _StructuredGroundedOutput(
        status="conflicting_evidence",
        segments=[],
        conflicts=[_StructuredConflict(
            description="退款期限不一致",
            evidence_ids=["E1", "E2"],
        )],
        reason="资料存在冲突，暂时无法确认退款期限。",
    )

    class FakeStructuredAgent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(output=output)

    answer = asyncio.run(GroundedAnswerGenerator(
        None,
        ModelProfile("test-model"),
        structured_agent=FakeStructuredAgent(),
    ).generate("退款期限？", (
        ContextCandidate("c1", "refund-v1", "退款期限七天。", 0, 7),
        ContextCandidate("c2", "refund-v2", "退款期限十四天。", 0, 8),
    )))

    assert answer.abstained is True
    assert answer.reason == "conflicting_evidence"
    assert answer.conflicts[0].citations == ("c1", "c2")


def test_generation_token_f1_accepts_paraphrase_overlap_without_exact_match():
    score = token_f1("Refunds require approval and return to the card.",
                     "Approved refunds are returned to the original card.")
    assert 0.0 < score < 1.0
    assert language_matches("How do I request a refund?", "You can submit the refund form.")
    assert not language_matches("How do I request a refund?", "您可以提交退款表单。")


def test_stage_metrics_identify_rerank_and_context_evidence_loss():
    document = RagDocument("refund", "退款", "退款审核通过后三到五个工作日原路退回。")
    chunks = project_chunks(
        [document], max_tokens=64, overlap_tokens=0,
        strategy=ChunkStrategy.STRUCTURE_AWARE,
    )
    evidence = EvidenceSpan("refund", 0, len(document.content), quote=document.content)
    case = RagCase(
        case_id="refund-1",
        group_id="refund",
        split="dev",
        query="钱多久回来",
        evidence=(evidence,),
        required_claims=("三到五个工作日",),
        forbidden_claims=("今天一定到账",),
    )
    trace = StageTrace(
        case_id=case.case_id,
        config_fingerprint="abc",
        query_variants=(QueryVariant("raw", case.query),),
        source_rankings={"raw:bm25": [chunks[0].chunk_id]},
        fused_chunk_ids=(chunks[0].chunk_id,),
        reranked_chunk_ids=(),
        packed_chunk_ids=(),
        answer="通常三到五个工作日。",
        citations=("refund",),
    )
    metrics = evaluate_stage_trace(case, trace, chunks)
    assert metrics["first_stage_evidence_recall"] == 1.0
    assert metrics["reranked_evidence_recall"] == 0.0
    assert metrics["rerank_harmful"] == 1.0
    assert metrics["required_claim_coverage"] == 1.0
    assert metrics["citation_precision"] == 1.0


def test_stage_metrics_count_evidence_items_not_duplicate_overlapping_chunks():
    document = RagDocument("policy", "Policy", "rule " * 400)
    chunks = project_chunks(
        [document], max_tokens=256, overlap_tokens=128,
        strategy=ChunkStrategy.FIXED_TOKENS,
    )
    evidence = EvidenceSpan("policy", 600, 700)
    matching = [chunk for chunk in chunks if chunk_contains_evidence(chunk, evidence)]
    assert len(matching) > 1
    case = RagCase("case", "group", "dev", "rule?", evidence=(evidence,))
    trace = StageTrace(
        case_id="case",
        config_fingerprint="abc",
        query_variants=(QueryVariant("raw", "rule?"),),
        source_rankings={},
        fused_chunk_ids=(matching[0].chunk_id,),
        reranked_chunk_ids=(matching[0].chunk_id,),
        packed_chunk_ids=(matching[0].chunk_id,),
    )

    assert evaluate_stage_trace(case, trace, chunks)["first_stage_evidence_recall"] == 1.0


def test_document_level_grounding_accepts_any_chunk_from_the_gold_document():
    documents = [
        RagDocument("gold", "Gold", "gold policy " * 500),
        RagDocument("other", "Other", "other policy " * 500),
    ]
    chunks = project_chunks(
        documents, max_tokens=128, overlap_tokens=0,
        strategy=ChunkStrategy.FIXED_TOKENS,
    )
    evidence = EvidenceSpan(
        "gold", 0, len(documents[0].content), granularity="document",
    )
    case = RagCase("case", "group", "dev", "policy?", evidence=(evidence,))
    gold_chunks = [chunk for chunk in chunks if chunk.document_id == "gold"]
    gold_chunk = gold_chunks[0]
    other_chunk = next(chunk for chunk in chunks if chunk.document_id == "other")

    assert evaluate_ranked_hits(
        case,
        [gold_chunk.chunk_id],
        {gold_chunk.chunk_id: {
            "document_id": "gold",
            "source_start_char": gold_chunk.start_char,
            "source_end_char": gold_chunk.end_char,
        }},
        top_k=1,
    )["evidence_recall"] == 1.0
    duplicate_hits = {
        chunk.chunk_id: {
            "document_id": "gold",
            "source_start_char": chunk.start_char,
            "source_end_char": chunk.end_char,
        }
        for chunk in gold_chunks[:2]
    }
    assert evaluate_ranked_hits(
        case, [chunk.chunk_id for chunk in gold_chunks[:2]], duplicate_hits, top_k=2,
    )["ndcg"] == 1.0
    assert evaluate_ranked_hits(
        case,
        [other_chunk.chunk_id],
        {other_chunk.chunk_id: {
            "document_id": "other",
            "source_start_char": other_chunk.start_char,
            "source_end_char": other_chunk.end_char,
        }},
        top_k=1,
    )["evidence_recall"] == 0.0


def test_selection_is_constraint_first_and_heldout_is_report_only():
    rows = [
        {"config_id": "unsafe-fast", "forbidden_rate": 0.1, "recall": 1.0, "latency": 1},
        {"config_id": "safe-slow", "forbidden_rate": 0.0, "recall": 0.95, "latency": 20},
        {"config_id": "safe-fast", "forbidden_rate": 0.0, "recall": 0.95, "latency": 10},
    ]
    kwargs = {
        "hard_constraints": {"forbidden_rate": ("eq", 0.0)},
        "metric_priority": [("recall", "max")],
        "cost_priority": ["latency"],
    }
    dev = select_configuration(rows, split="dev", **kwargs)
    heldout = select_configuration(rows, split="heldout", **kwargs)
    assert dev["recommended"] == "safe-fast"
    assert heldout["recommended"] is None
    assert heldout["selection_allowed"] is False


def test_raw_query_capture_needs_no_model_and_preserves_dataset_identity():
    document = RagDocument("policy", "Policy", "Policy text")
    case = RagCase(
        "case", "group", "dev", "What is the policy?",
        evidence=(EvidenceSpan("policy", 0, len(document.content)),),
        query_types=("customer_support", "long_document"),
    )
    dataset = SimpleNamespace(
        manifest={"dataset_id": "long-dev"},
        select_cases=lambda split: (case,) if split == "dev" else (),
    )

    capture = capture_raw_queries(dataset, split="dev", max_cases=1)

    assert capture["dataset_id"] == "long-dev"
    assert capture["total_calls"] == 0
    assert capture["rows"][0]["raw_query"] == case.query
    assert capture["rows"][0]["standalone"] == ""


def test_paired_bootstrap_keeps_groups_together_and_reports_direction():
    candidate = [{"recall": 1.0}, {"recall": 1.0}, {"recall": 0.5}]
    baseline = [{"recall": 0.0}, {"recall": 0.0}, {"recall": 0.5}]
    result = paired_group_bootstrap_delta(
        candidate,
        baseline,
        group_ids=["same-dialogue", "same-dialogue", "other-dialogue"],
        metric="recall",
        samples=200,
        seed=7,
    )
    assert result["delta"] == 2 / 3
    assert result["ci95_low"] >= 0.0
    assert result["ci95_high"] > 0.0


def test_doc2dial_adapter_preserves_official_source_spans(tmp_path):
    document_text = "Reset the password using the verified email."
    doc_data = {"doc_data": {}}
    dial_data = {"dial_data": {}}
    for domain in ("dmv", "ssa", "studentaid", "va"):
        doc_id = f"{domain}-doc"
        doc_data["doc_data"][domain] = {doc_id: {
            "title": "Password reset",
            "doc_text": document_text,
            "spans": {"1": {
                "start_sp": 0,
                "end_sp": len(document_text),
                "text_sp": document_text,
            }},
        }}
        dial_data["dial_data"][domain] = {doc_id: [{
            "dial_id": f"{domain}-dial",
            "turns": [
                {"turn_id": 1, "role": "user", "utterance": "How do I reset it?", "references": []},
                {"turn_id": 2, "role": "agent", "utterance": document_text,
                 "references": [{"sp_id": "1", "label": "solution"}]},
            ],
        }]}
    archive = tmp_path / "doc2dial.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("doc2dial_doc.json", json.dumps(doc_data))
        bundle.writestr("doc2dial_dial_validation.json", json.dumps(dial_data))
    dataset = build_subset(
        archive, tmp_path / "dataset",
        max_documents=4, max_cases=4, split="dev",
    )
    assert len(dataset.documents) == 4
    assert len(dataset.cases) == 4
    for case in dataset.cases:
        evidence = case.evidence[0]
        document = next(item for item in dataset.documents if item.document_id == evidence.document_id)
        assert document.content[evidence.start_char:evidence.end_char] == evidence.quote

    long_slice = build_subset(
        archive, tmp_path / "long-dataset",
        max_documents=4, max_cases=4, split="dev",
        min_relevant_document_chars=10,
    )
    assert long_slice.manifest["dataset_id"] == "doc2dial-rag-long10-dev-v1"
    assert all("long_document" in case.query_types for case in long_slice.cases)
    assert long_slice.manifest["source"]["grounding_granularity"] == "character-span"
