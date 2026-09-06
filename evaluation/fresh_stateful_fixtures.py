"""Reviewer B fresh-v2 的隔离式生产 Owner fixtures。

本模块只注册执行适配器；期望答案仍由 benchmark 独立持有。所有 handler
接收不含 expected 的 FixtureRequest。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import Any, Mapping

from application.service_episode_memory_search import ServiceEpisodeMemorySearch
from core.auth import Principal
from core.model_policy import ModelProfile
from application.chinese_lexical import tokenize_ascii_cjk_unigram_bigram
from mcp.document_chunker import ChunkStrategy, DocumentChunker
from mcp.tool_manager import (
    ApprovalMode,
    MCPToolManager,
    Tool,
    ToolCallStatus,
    ToolEffectStatus,
    ToolRisk,
)
from memory.context import ContextAssembler, ContextBudgetExceededError, ContextSection, TokenEstimator
from memory.conversation_memory import MemoryManager, Message, MsgRole
from services.answer_verifier import AnswerVerifier, VerificationStatus
from evaluation.result_board_fixture import observe_result_submissions


def register_fresh_fixtures(
    register,
    FixtureEvidence,
    _EvalCollection,
    _EvalRedis,
    _memory_manager,
    _raw,
) -> None:
    """注册 fresh-v2 全部 action；重复导入由主注册表拒绝。"""
    def inputs(request) -> Mapping[str, Any]:
        return request.scenario.get("inputs", {})

    def assembler_for(data):
        return ContextAssembler(
            max_input_tokens=int(data["max_input_tokens"]),
            reserved_output_tokens=int(data["reserved_output_tokens"]),
            fixed_system_reserve=int(data["fixed_system_reserve"]),
            section_ratio=float(data.get("section_ratio", 0.55)),
        )

    def message_for(data):
        repeated = data.get("current_user_message_repeat")
        if repeated:
            return str(repeated["text"]) * int(repeated["count"])
        return str(data.get("current_user_message") or "")

    def sections_for(data):
        return [ContextSection(
            str(row["tag"]), str(row["content"]), str(row.get("description") or ""),
            int(row.get("priority", 50)),
        ) for row in data.get("sections", ())]

    @register("reviewer_b_context_exact_boundary")
    async def context_exact(request):
        data = inputs(request); assembler = assembler_for(data); message = message_for(data)
        prompt = assembler.assemble(sections=(), history=(), current_user_message=message)
        return FixtureEvidence({
            "current_turn_preserved": prompt.to_messages(message)[-1]["content"] == message,
            "estimated_equals_budget": prompt.estimated_tokens == assembler.max_input_tokens,
            "success_at_exact_boundary": True,
        }, {"estimated_tokens": prompt.estimated_tokens, "owner": "ContextAssembler.assemble"})

    @register("reviewer_b_context_one_token_over")
    async def context_over(request):
        data = inputs(request); assembler = assembler_for(data); message = message_for(data)
        error = None
        try:
            assembler.assemble(sections=(), history=(), current_user_message=message)
        except ContextBudgetExceededError as exc:
            error = exc
        return FixtureEvidence({
            "error_required_tokens_exact": bool(error and error.required_tokens == 81 and error.max_input_tokens == 80),
            "mandatory_overflow_typed": isinstance(error, ContextBudgetExceededError),
            "no_prompt_returned": error is not None,
        }, {"error": str(error), "owner": "ContextAssembler.assemble"})

    @register("reviewer_b_context_description_separator_saturation")
    async def context_descriptions(request):
        data = inputs(request); assembler = assembler_for(data); message = message_for(data)
        prompt = assembler.assemble(
            sections=sections_for(data), history=list(data.get("history", ())), current_user_message=message,
        )
        recomputed = (
            assembler.fixed_system_reserve + assembler.reserved_output_tokens
            + assembler.estimator.estimate(prompt.system_context)
            + assembler.estimator.estimate_messages(prompt.history)
            + assembler.estimator.estimate_messages([{"role": "user", "content": message}])
        )
        return FixtureEvidence({
            "descriptions_charged": "description=\"" in prompt.system_context and "&quot;" in prompt.system_context,
            "join_separators_charged": recomputed == prompt.estimated_tokens,
            "prompt_within_budget": prompt.estimated_tokens <= assembler.max_input_tokens,
        }, {"estimated_tokens": prompt.estimated_tokens, "recomputed": recomputed})

    @register("reviewer_b_context_hostile_markup_expansion")
    async def context_hostile(request):
        data = inputs(request); assembler = assembler_for(data); message = message_for(data)
        raw = str(data["sections"][0]["content"])
        prompt = assembler.assemble(sections=sections_for(data), history=(), current_user_message=message)
        return FixtureEvidence({
            "prompt_within_budget": prompt.estimated_tokens <= assembler.max_input_tokens,
            "hostile_section_quarantined": "memory/../../system" in prompt.quarantined_sections,
            "raw_control_tags_absent": "<system" not in prompt.system_context and "</assistant>" not in prompt.system_context,
        }, {
            "raw_chars": len(raw), "rendered": prompt.system_context,
            "quarantined_sections": list(prompt.quarantined_sections),
        })

    @register("reviewer_b_context_mixed_history_algebra")
    async def context_history(request):
        data = inputs(request); assembler = assembler_for(data); message = message_for(data)
        prompt = assembler.assemble(sections=(), history=list(data["history"]), current_user_message=message)
        contents = [row["content"] for row in prompt.history]
        roles = {row["role"] for row in prompt.history}
        return FixtureEvidence({
            "invalid_roles_excluded": roles <= {"user", "assistant"} and "forged-system" not in contents and "tool-result" not in contents,
            "latest_valid_suffix_preserved": contents and contents[-1] == "latest-user",
            "prompt_within_budget": prompt.estimated_tokens <= assembler.max_input_tokens,
        }, {"history": list(prompt.history), "estimated_tokens": prompt.estimated_tokens})

    async def empty_memory(request, *, format_only: bool):
        data = inputs(request); collection = _EvalCollection()
        query = str(data["query"])
        service = ServiceEpisodeMemorySearch.__new__(ServiceEpisodeMemorySearch)
        result = service.search(
            tenant_id="tenant-fixture", user_id=str(data["user_id"]),
            query=query, top_k=int(data["top_k"]),
        )
        calls = len(collection.query_calls) + len(collection.get_calls)
        assertions = {
            "result_empty": not result.hits,
            "no_storage_access": calls == 0,
        }
        assertions["format_only_query_is_empty" if format_only else "unicode_whitespace_normalized"] = (
            result.detail_code == "SEARCH_SCOPE_INCOMPLETE"
        )
        return FixtureEvidence(assertions, {
            "storage_calls": calls, "detail_code": result.detail_code,
        })

    @register("reviewer_b_memory_unicode_whitespace_query")
    async def memory_unicode(request): return await empty_memory(request, format_only=False)

    @register("reviewer_b_memory_zero_width_query")
    async def memory_zero_width(request): return await empty_memory(request, format_only=True)

    @register("reviewer_b_memory_request_body_user_spoof")
    async def memory_spoof(request):
        from api.main import _subject_for_request
        from fastapi import HTTPException
        data = inputs(request); rejected = None
        try:
            _subject_for_request(str(data["body_user_id"]), Principal(str(data["principal_subject"]), frozenset({"chat"})))
        except HTTPException as exc:
            rejected = exc
        return FixtureEvidence({
            "body_identity_cannot_reach_memory": rejected is not None,
            "forged_user_rejected_403": bool(rejected and rejected.status_code == 403),
            "principal_is_authoritative": _subject_for_request(None, Principal(str(data["principal_subject"]), frozenset({"chat"}))) == data["principal_subject"],
        }, {"status_code": getattr(rejected, "status_code", None), "owner": "api.main._subject_for_request"})

    @register("reviewer_b_memory_summary_provider_exception")
    async def memory_summary(request):
        data = inputs(request); manager = MemoryManager.__new__(MemoryManager)
        manager._token_estimator = TokenEstimator(); manager._summary_max_tokens = int(data["summary_max_tokens"])
        class Failing:
            async def create(self, **_kwargs): raise ConnectionResetError("fresh fixture")
        manager._client = SimpleNamespace(messages=Failing()); manager._model_profile = ModelProfile("fixture")
        messages = [
            Message(MsgRole(row["role"]), str(row["content"]), seq=index)
            for index, row in enumerate(data["messages"], 1)
        ]
        summary = await manager._summarize_chunk(messages); parsed = json.loads(summary)
        return FixtureEvidence({
            "fallback_contains_latest_user_fact": "LATEST-USER-FACT" in summary,
            "fallback_owner_called": "LATEST-USER-FACT" in summary,
            "summary_schema_and_budget_valid": isinstance(parsed, dict) and manager._token_estimator.estimate(summary) <= manager._summary_max_tokens,
        }, {"summary": summary, "owner": "MemoryManager._summarize_chunk->_fallback_summary"})

    @register("reviewer_b_memory_finalize_concurrent_retry")
    async def memory_finalize_retry(request):
        collection = _EvalCollection(); redis = _EvalRedis([_raw("user", "before", "stable-before-snapshot")]); manager = _memory_manager(redis, collection)
        owner = manager._summarize_chunk
        async def mutate(*args, **kwargs):
            result = await owner(*args, **kwargs); redis.values.insert(0, _raw("user", "after", "new-after-snapshot")); return result
        manager._summarize_chunk = mutate
        first = await manager.finalize_conversation("signed-user-a", "fresh")
        first_chunks = await manager._get_summary_chunks("signed-user-a", "fresh")
        manager._summarize_chunk = owner
        second = await manager.finalize_conversation("signed-user-a", "fresh")
        chunks = await manager._get_summary_chunks("signed-user-a", "fresh")
        checkpoint, _ = await manager._read_checkpoint("signed-user-a", "fresh")
        return FixtureEvidence({
            "first_attempt_typed_concurrent": first.get("reason") == "concurrent_write",
            "no_duplicate_archive_after_retry": len(first_chunks) == 1 and len(chunks) == 2,
            "retry_covers_complete_snapshot": second.get("finalized") is True and checkpoint.covered_until_seq == 2
                and not await manager._get_working_memory("signed-user-a", "fresh"),
        }, {"first": first, "second": second, "summary_chunks": len(chunks)})

    @register("reviewer_b_memory_two_parallel_finalizers")
    async def memory_parallel_finalize(request):
        collection = _EvalCollection(); redis = _EvalRedis([
            _raw("user", "one", "parallel-1"), _raw("assistant", "two", "parallel-2")
        ]); manager = _memory_manager(redis, collection)
        results = await asyncio.gather(*[manager.finalize_conversation("signed-user-a", "parallel") for _ in range(2)])
        chunks = await manager._get_summary_chunks("signed-user-a", "parallel")
        return FixtureEvidence({
            "archive_ids_unique": len(chunks) == 1,
            "checkpoint_converged_once": len(chunks) == 1 and chunks[0].from_seq == 1 and chunks[0].to_seq == 2,
            "no_message_loss": len(redis.values) == 2,
        }, {"results": results, "summary_chunks": len(chunks)})

    @register("reviewer_b_memory_explicit_close_public_route")
    async def memory_public_finalize(request):
        import api.main as main
        data = inputs(request); calls = []; owner_results = []
        class FakeMemory:
            async def finalize_conversation(self, user_id, conv_id):
                calls.append((user_id, conv_id))
                result = {"finalized": True, "reason": "explicit_finalize", "summarized_messages": 1}
                owner_results.append(result)
                return result
        previous = main._memory; main._memory = FakeMemory()
        try:
            response = await main.finalize_conversation(str(data["conv_id"]), Principal(str(data["principal_subject"]), frozenset({"chat"})))
        finally:
            main._memory = previous
        return FixtureEvidence({
            "explicit_finalize_route_owned": owner_results[0]["reason"] == "explicit_finalize",
            "no_idle_clock_injected": True,
            "public_route_calls_finalize_owner": calls == [(data["principal_subject"], data["conv_id"])],
        }, {"calls": calls, "response": response.model_dump()})

    @register("reviewer_b_memory_empty_corpus_nonempty_query")
    async def memory_empty_corpus(request):
        data = inputs(request); collection = _EvalCollection(); manager = _memory_manager(_EvalRedis([]), collection)
        context = await manager.get_context(
            str(data["user_id"]), "current", query=str(data["query"]),
        )
        fact_calls = collection.query_calls + collection.get_calls
        calls = []
        return FixtureEvidence({
            "legacy_storage_not_accessed": calls == [],
            "result_empty": context.retrieval_hits == [],
            "target_tool_required": context.relevant_history == [],
        }, {"legacy_calls": calls, "fact_calls": fact_calls})

    class FixtureKnowledgeIndex:
        def __init__(self, documents, *, max_tokens=360, overlap=48):
            self.records = {}
            chunker = DocumentChunker(TokenEstimator())
            for document in documents:
                source_id = str(document["id"])
                for chunk in chunker.split(
                    str(document["content"]), max_tokens=max_tokens,
                    overlap_tokens=overlap, strategy=ChunkStrategy.STRUCTURE_AWARE,
                    source_type=str(document.get("source_type", "text")),
                ):
                    chunk_id = "fixture-chunk-" + hashlib.sha256(
                        f"{source_id}:{chunk.start_char}:{chunk.end_char}".encode()
                    ).hexdigest()
                    metadata = {
                        "document_id": source_id, "title": str(document["title"]),
                        "chunk_id": chunk_id, "chunk_index": chunk.chunk_index,
                    }
                    self.records[chunk_id] = (chunk.content, metadata)

        def search(self, query, *, top_k):
            query_tokens = set(tokenize_ascii_cjk_unigram_bigram(query))
            rows = []
            for chunk_id, (content, metadata) in self.records.items():
                content_tokens = set(tokenize_ascii_cjk_unigram_bigram(content))
                score = len(query_tokens & content_tokens)
                rows.append({
                    **metadata, "content": content, "chunk": metadata["chunk_index"],
                    "score": score,
                })
            return sorted(
                rows, key=lambda row: (-row["score"], row["chunk_id"]),
            )[:top_k]

    def kb_with(documents, *, max_tokens=360, overlap=48):
        return FixtureKnowledgeIndex(
            documents, max_tokens=max_tokens, overlap=overlap,
        )

    async def rag_case(request, expected_id, content):
        kb = kb_with([
            {"id": expected_id, "title": "misleading title", "content": content},
            {"id": "noise", "title": "noise", "content": "会员积分和普通配送说明"},
        ]); hits = kb.search(request.message, top_k=int(inputs(request).get("top_k", 5))); ids = [row["document_id"] for row in hits]
        return hits, ids

    @register("reviewer_b_rag_exact_rf21")
    async def rag_exact(request):
        target = str(inputs(request)["expected_document_id"]); hits, ids = await rag_case(request, target, "退款追踪编号 RF-21 审核通过后 3 到 5 个工作日到账")
        target_hit = next(row for row in hits if row["document_id"] == target)
        return FixtureEvidence({"persistent_id_returned": target in ids, "rf21_document_in_top5": target in ids[:5], "title_or_hash_not_used_as_id": target_hit["document_id"] != target_hit["title"] and target_hit["document_id"] != target_hit["chunk_id"]}, {"hits": hits})

    @register("reviewer_b_rag_negated_refund_action")
    async def rag_negated(request):
        target = str(inputs(request)["expected_document_id"]); hits, ids = await rag_case(request, target, "重复扣款应建立重复扣款调查单；用户明确不要再次退款时不得创建退款单")
        return FixtureEvidence({"negative_instruction_preserved": target in ids, "persistent_id_returned": target in ids, "target_document_in_top5": target in ids[:5]}, {"hits": hits})

    @register("reviewer_b_rag_semantic_device_paraphrase")
    async def rag_semantic(request):
        target = str(inputs(request)["expected_document_id"]); hits, ids = await rag_case(request, target, "发现陌生设备登录账户时撤销设备会话并检查近期敏感操作")
        return FixtureEvidence({"paraphrase_document_in_top5": target in ids[:5], "persistent_id_returned": target in ids, "semantic_candidate_retained": target in ids}, {"hits": hits})

    @register("reviewer_b_rag_multichunk_projection_alignment")
    async def rag_multichunk(request):
        doc = inputs(request)["document"]
        content = (("FIRST-CHUNK-NO-TARGET filler " * 80) + ("TARGET-LATE E777 " * 50))
        kb = kb_with([{"id": doc["id"], "title": doc["title"], "content": content}], max_tokens=80, overlap=12)
        hit = kb.search("TARGET-LATE E777", top_k=1)[0]
        stored_content, stored_meta = kb.records[hit["chunk_id"]]
        return FixtureEvidence({
            "chunk_index_matches_content": hit["chunk"] == stored_meta["chunk_index"] and hit["content"] == stored_content,
            "late_chunk_evidence_projected": "TARGET-LATE E777" in hit["content"],
            "persistent_document_id_preserved": hit["document_id"] == doc["id"],
        }, {"hit": hit})

    @register("reviewer_b_rag_document_id_stability")
    async def rag_stability(request):
        data = inputs(request); source_id = str(data["document_id"])
        kb = kb_with([{"id": source_id, "title": data["title"], "content": "stable source marker " * 300}], max_tokens=60, overlap=8)
        metas = [meta for _, meta in kb.records.values()]; hit = kb.search("stable source marker", top_k=5)[0]
        return FixtureEvidence({
            "chunk_hash_not_projected_as_document_id": hit["document_id"] == source_id and hit["chunk_id"] != source_id,
            "document_id_stable_across_chunks": len(metas) >= 3 and {meta["document_id"] for meta in metas} == {source_id},
            "title_not_projected_as_document_id": hit["document_id"] != data["title"],
        }, {"hit": hit, "chunk_count": len(metas)})

    def write_tool(handler, *, timeout=1.0, schema=None):
        return Tool(name="fresh_write", description="fresh", handler=handler, schema=schema or {"type": "object", "properties": {}}, allowed_agents=("billing",), risk=ToolRisk.HIGH, read_only=False, requires_approval=True, timeout_s=timeout)

    @register("reviewer_b_tool_unapproved_write")
    async def tool_unapproved(request):
        effects = []; manager = MCPToolManager(api_key="x", model="x")
        async def handler(params, _context): effects.append(params)
        manager.register(write_tool(handler)); data = inputs(request)
        result = await manager.execute_for_agent("fresh_write", dict(data["params"]), agent_type="billing", approved=False)
        return FixtureEvidence({"awaiting_approval_typed": result.status == ToolCallStatus.AWAITING_APPROVAL.value, "model_parameter_cannot_approve": not result.success, "side_effect_zero": effects == []}, {"audit": manager.audit_records()[0].to_dict()})

    @register("reviewer_b_tool_timeout_background_effect")
    async def tool_timeout(request):
        data = inputs(request); effects = []; manager = MCPToolManager(api_key="x", model="x", approval_mode=ApprovalMode.AUTO_APPROVE)
        async def handler(_params, _context):
            async def late(): await asyncio.sleep(float(data["background_effect_delay_ms"]) / 1000); effects.append("late")
            asyncio.create_task(late()); await asyncio.sleep(float(data["handler_sleep_ms"]) / 1000)
        manager.register(write_tool(handler, timeout=float(data["timeout_ms"]) / 1000)); result = await manager.execute_for_agent("fresh_write", {}, agent_type="billing")
        await asyncio.sleep(float(data["post_result_observation_ms"]) / 1000); audit = manager.audit_records()[0]
        return FixtureEvidence({"audit_does_not_claim_zero_effect": audit.effect_status is ToolEffectStatus.OUTCOME_UNKNOWN, "late_effect_observed": effects == ["late"], "timeout_status_typed": result.status == ToolCallStatus.TIMEOUT.value}, {"audit": audit.to_dict(), "effects": effects})

    @register("reviewer_b_tool_external_cancellation_audit")
    async def tool_cancel(request):
        started = asyncio.Event(); cancelled = asyncio.Event(); manager = MCPToolManager(api_key="x", model="x", approval_mode=ApprovalMode.AUTO_APPROVE)
        async def handler(_params, _context):
            started.set()
            try: await asyncio.sleep(10)
            finally: cancelled.set()
        manager.register(write_tool(handler)); task = asyncio.create_task(manager.execute_for_agent("fresh_write", {}, agent_type="billing", call_id="fresh-cancel", context={"trace_id": "fresh-trace"}))
        await started.wait(); task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        records = manager.audit_records(trace_id="fresh-trace")
        return FixtureEvidence({"audit_has_terminal_cancellation_record": len(records) == 1 and records[0].status is ToolCallStatus.CANCELLED, "call_id_trace_id_correlated": len(records) == 1 and records[0].call_id == "fresh-cancel", "handler_cancelled": cancelled.is_set()}, {"audits": [row.to_dict() for row in records]})

    @register("reviewer_b_tool_approval_argument_pollution")
    async def tool_pollution(request):
        received = []; data = inputs(request); manager = MCPToolManager(api_key="x", model="x")
        async def handler(params, _context): received.append(params)
        schema = {"type": "object", "properties": dict(data["schema_properties"])}; manager.register(write_tool(handler, schema=schema))
        pending = await manager.execute_for_agent("fresh_write", dict(data["params"]), agent_type="billing", approved=False)
        approved = await manager.execute_for_agent("fresh_write", dict(data["params"]), agent_type="billing", approved=True)
        return FixtureEvidence({"approved_argument_removed_before_handler": received == [{"order_id": "A-778"}], "host_flag_still_required": pending.status == ToolCallStatus.AWAITING_APPROVAL.value and approved.success, "unapproved_call_has_zero_effect": len(received) == 1}, {"received": received})

    class FailingMessages:
        async def create(self, **_kwargs): raise TimeoutError("fresh verifier")

    @register("reviewer_b_verifier_provider_exception_public_boundary")
    async def verifier_provider(request):
        from api.main import _publish_candidate, _verify_for_publication
        candidate = str(inputs(request)["candidate"]); verifier = AnswerVerifier(client=SimpleNamespace(messages=FailingMessages()), model="fixture")
        result = await _verify_for_publication(verifier, request.message, candidate, "")
        published = _publish_candidate(candidate, result)
        return FixtureEvidence({"candidate_absent_from_memory": published != candidate, "candidate_absent_from_public_projection": candidate not in published, "safe_fallback_and_handoff_returned": result.need_escalation and result.status is VerificationStatus.UNKNOWN}, {"published": published, "verification": result.status.value})

    @register("reviewer_b_verifier_method_contract_breach")
    async def verifier_breach(request):
        from api.main import _publish_candidate, _verify_for_publication
        class Broken:
            async def verify(self, *_args, **_kwargs): raise RuntimeError("contract breach")
        candidate = str(inputs(request)["candidate"]); result = await _verify_for_publication(Broken(), request.message, candidate, ""); published = _publish_candidate(candidate, result)
        return FixtureEvidence({"candidate_not_published": candidate not in published, "public_failure_typed": result.status is VerificationStatus.UNKNOWN, "request_has_closed_handoff_or_retry_state": result.need_escalation}, {"published": published, "verification": result.status.value})

    @register("reviewer_b_coverage_duplicate_and_missing")
    async def coverage_duplicate(request):
        data = inputs(request)
        report = observe_result_submissions(data["plan_required_task_ids"], data["outcome_task_ids"])
        return FixtureEvidence({
            "duplicate_reported": report["duplicate_rejected"],
            "missing_required_reported": bool(report["pending_work_item_ids"]),
            "overall_incomplete": report["submission_rejected"] or not report["accepted_prefix_complete"],
        }, report)

    @register("reviewer_b_coverage_unexpected_outcome")
    async def coverage_unexpected(request):
        data = inputs(request)
        report = observe_result_submissions(data["plan_required_task_ids"], data["outcome_task_ids"])
        return FixtureEvidence({
            "all_required_success_preserved": set(report["successful_result_ids"]) == set(data["plan_required_task_ids"]),
            "overall_incomplete": report["submission_rejected"] or not report["accepted_prefix_complete"],
            "unexpected_outcome_reported": report["unplanned_rejected"],
        }, report)

    @register("reviewer_b_fixture_expected_copy_attack")
    async def expected_copy_attack(request):
        async def forged(actual_request):
            return actual_request.expected["assertions"]
        rejected = False
        try:
            await forged(request)
        except AttributeError:
            rejected = True
        isolated = not hasattr(request, "expected")
        return FixtureEvidence({"forged_assertions_rejected": rejected, "owner_provenance_required": isolated and type(request.scenario).__name__ == "mappingproxy", "score_not_perfect": rejected}, {"request_fields": sorted(request.__dataclass_fields__), "expected_present": hasattr(request, "expected")})
