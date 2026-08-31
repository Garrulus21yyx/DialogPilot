import json
import asyncio
import zipfile
from types import SimpleNamespace

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
from evaluation.rag_pipeline.metrics import project_chunks
from mcp.document_chunker import ChunkStrategy
from mcp.query_transformer import QueryTransformer
from mcp.result_reranker import RerankCandidate, ResultReranker
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.grounded_answer_generator import GroundedAnswerGenerator
from evaluation.rag_generation_evaluation import language_matches, token_f1
from core.model_policy import ModelProfile
from evaluation.rag_pipeline.selection import paired_group_bootstrap_delta
from scripts.build_doc2dial_rag_subset import build_subset
from evaluation.rag_query_ablation import _variants, _weights


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
    variants = _variants(row, "all")
    weights = _weights(
        variants, raw_mass=0.5, lexical_weight=0.75, vector_weight=0.25,
    )
    assert sum(weights.values()) == 1.0
    assert weights["raw:bm25"] + weights["raw:vector"] == 0.5
    assert "hyde:bm25" not in weights
    assert weights["hyde:vector"] > 0


def test_reranker_validates_ids_and_completes_partial_permutation():
    class FakeMessages:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(
                    type="text", text='{"ordered_ids":["c2","invented","c2"]}',
                )],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

    reranker = ResultReranker(
        SimpleNamespace(messages=FakeMessages()), ModelProfile("test-model"),
    )
    result = asyncio.run(reranker.rerank("refund", (
        RerankCandidate("c1", "general information"),
        RerankCandidate("c2", "refund policy"),
        RerankCandidate("c3", "contact information"),
    )))
    assert result.model_ordered_ids == ("c2",)
    assert result.ordered_ids == ("c2", "c1", "c3")


def test_reranker_accepts_typed_bare_id_array_from_provider():
    class FakeMessages:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text='["c2", "c1"]')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    result = asyncio.run(ResultReranker(
        SimpleNamespace(messages=FakeMessages()), ModelProfile("test-model"),
    ).rerank("refund", (
        RerankCandidate("c1", "general"), RerankCandidate("c2", "refund policy"),
    )))
    assert result.error is None
    assert result.ordered_ids == ("c2", "c1")


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


def test_grounded_generator_validates_citations_and_fails_closed():
    class FakeMessages:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(
                    type="text",
                    text='{"answer":"退款需要审核。","citations":["unknown"],"abstained":false}',
                )],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    generator = GroundedAnswerGenerator(
        SimpleNamespace(messages=FakeMessages()), ModelProfile("test-model"),
    )
    answer = asyncio.run(generator.generate("退款", (
        ContextCandidate("c1", "doc", "退款需要审核。", 0, 7),
    )))
    assert answer.abstained is True
    assert answer.citations == ()
    assert answer.error == "ValueError"


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
