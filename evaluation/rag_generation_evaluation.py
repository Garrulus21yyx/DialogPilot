"""Generate grounded customer-support answers and evaluate citations plus an LLM judge."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from anthropic import AsyncAnthropic

from core.llm_metrics import capture_llm_usage, create_message
from core.llm_utils import extract_text_content
from core.model_policy import ModelPolicy, ModelRole
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import chunk_contains_evidence, project_chunks
from mcp.context_packer import ContextCandidate
from mcp.grounded_answer_generator import (
    GROUNDED_GENERATION_PROMPT_VERSION,
    GroundedAnswerGenerator,
)


JUDGE_PROMPT_VERSION = "customer-support-rag-judge-v2"
_TOKENS = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def token_f1(answer: str, reference: str) -> float:
    left = [item.lower() for item in _TOKENS.findall(answer)]
    right = [item.lower() for item in _TOKENS.findall(reference)]
    if not left or not right:
        return float(left == right)
    left_counts = {token: left.count(token) for token in set(left)}
    right_counts = {token: right.count(token) for token in set(right)}
    common = sum(min(count, right_counts.get(token, 0)) for token, count in left_counts.items())
    precision, recall = common / len(left), common / len(right)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def language_matches(query: str, answer: str) -> bool:
    """Coarse deterministic guard against system-prompt language leakage."""
    def dominant(text: str) -> str:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        return "cjk" if cjk > latin else "latin"
    return dominant(query) == dominant(answer)


async def _judge(
    client: Any,
    profile: Any,
    *,
    query: str,
    answer: str,
    reference: str,
    cited_context: Sequence[str],
) -> tuple[dict[str, bool] | None, str | None]:
    prompt = f"""你是客服 RAG 评测员。只判断，不改写答案。
grounded：答案中的可核查事实是否都能由引用资料推出。
correct：答案是否正确处理用户问题，并与参考客服回复的核心意图一致；允许合理改写。
complete：答案是否覆盖参考回复中解决当前问题所需的核心信息。
citations_relevant：每一段引用资料是否都对答案中的至少一个具体陈述有实质支持，而非仅主题相似。
返回严格 JSON：{{"grounded":true,"correct":true,"complete":true,"citations_relevant":true}}。

用户问题：{json.dumps(query, ensure_ascii=False)}
待评答案：{json.dumps(answer, ensure_ascii=False)}
参考客服回复：{json.dumps(reference, ensure_ascii=False)}
引用资料：{json.dumps(list(cited_context), ensure_ascii=False)}"""
    try:
        response = await create_message(
            client, profile, ModelRole.JUDGE,
            max_tokens=384, temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = extract_text_content(response.content)
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("judge response did not contain JSON")
        payload = json.loads(raw[start:end + 1])
        keys = ("grounded", "correct", "complete", "citations_relevant")
        if not isinstance(payload, dict) or any(not isinstance(payload.get(key), bool) for key in keys):
            raise ValueError("judge response fields have invalid types")
        return {key: payload[key] for key in keys}, None
    except Exception as exc:
        return None, type(exc).__name__


async def run_generation_evaluation(
    dataset: RagDataset,
    packing_report: Mapping[str, Any],
    *,
    concurrency: int = 2,
) -> dict[str, Any]:
    policy = ModelPolicy.from_env()
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if policy.base_url:
        kwargs["base_url"] = policy.base_url
    client = AsyncAnthropic(**kwargs)
    synthesis_profile = policy.profile(ModelRole.SYNTHESIS)
    judge_profile = policy.profile(ModelRole.JUDGE)
    generator = GroundedAnswerGenerator(client, synthesis_profile)
    cases = {case.case_id: case for case in dataset.cases}
    chunks = project_chunks(
        dataset.documents, max_tokens=512, overlap_tokens=64, strategy="fixed_tokens",
    )
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(row: Mapping[str, Any]) -> dict[str, Any]:
        case = cases[str(row["case_id"])]
        contexts = tuple(ContextCandidate(
            chunk_id=chunk_id,
            document_id=chunks_by_id[chunk_id].document_id,
            text=chunks_by_id[chunk_id].content,
            start_char=chunks_by_id[chunk_id].start_char,
            end_char=chunks_by_id[chunk_id].end_char,
        ) for chunk_id in row["packed_chunk_ids"])
        async with semaphore:
            with capture_llm_usage() as generation_usage:
                answer = await generator.generate(case.query, contexts, history=case.history)
            cited_text = [chunks_by_id[item].content for item in answer.citations]
            with capture_llm_usage() as judge_usage:
                judgement, judge_error = await _judge(
                    client, judge_profile,
                    query=case.query,
                    answer=answer.answer,
                    reference=case.required_claims[0] if case.required_claims else "",
                    cited_context=cited_text,
                )
        cited_gold = {
            chunk_id for chunk_id in answer.citations
            if any(chunk_contains_evidence(chunks_by_id[chunk_id], evidence) for evidence in case.evidence)
        }
        context_gold = {
            item.chunk_id for item in contexts
            if any(chunk_contains_evidence(chunks_by_id[item.chunk_id], evidence) for evidence in case.evidence)
        }
        reference = case.required_claims[0] if case.required_claims else ""
        return {
            "case_id": case.case_id,
            "answer": answer.answer,
            "citations": list(answer.citations),
            "abstained": answer.abstained,
            "generation_error": answer.error,
            "reference": reference,
            "token_f1": token_f1(answer.answer, reference),
            "language_match": float(language_matches(case.query, answer.answer)),
            "gold_evidence_citation_precision": (
                len(cited_gold) / len(answer.citations) if answer.citations else 0.0
            ),
            "evidence_cited": float(bool(cited_gold)),
            "context_contains_evidence": float(bool(context_gold)),
            "judge": judgement,
            "judge_error": judge_error,
            "generation_usage": generation_usage.summary()["total"],
            "judge_usage": judge_usage.summary()["total"],
        }

    rows = list(await asyncio.gather(*(
        one(row) for row in packing_report["recommended_rows"]
    )))
    if not rows:
        raise ValueError(
            "packing report contains no rows; heldout evaluation requires an explicit frozen config"
        )
    judge_valid = [row for row in rows if row["judge"] is not None]
    context_answerable = [row for row in rows if row["context_contains_evidence"]]
    generator_failures = sum(row["generation_error"] is not None for row in rows)
    judge_failures = len(rows) - len(judge_valid)
    gold_precision_rows = [row for row in rows if row["context_contains_evidence"]]
    gold_citation_precision = (
        statistics.fmean(row["gold_evidence_citation_precision"] for row in gold_precision_rows)
        if gold_precision_rows else 1.0
    )
    evidence_citation_recall = (
        statistics.fmean(row["evidence_cited"] for row in context_answerable)
        if context_answerable else 1.0
    )
    grounded_rate = statistics.fmean(float(row["judge"]["grounded"]) for row in judge_valid)
    correct_rate = statistics.fmean(float(row["judge"]["correct"]) for row in judge_valid)
    complete_rate = statistics.fmean(float(row["judge"]["complete"]) for row in judge_valid)
    citation_relevance_rate = statistics.fmean(
        float(row["judge"]["citations_relevant"]) for row in judge_valid
    )
    language_match_rate = statistics.fmean(row["language_match"] for row in rows)
    gates = {
        "generator_failure_rate_le_1pct": generator_failures / len(rows) <= 0.01,
        "judge_failure_rate_le_5pct": judge_failures / len(rows) <= 0.05,
        "language_match_rate_ge_95pct": language_match_rate >= 0.95,
        "evidence_citation_recall_ge_90pct_when_available": evidence_citation_recall >= 0.90,
        "judge_grounded_rate_ge_95pct": grounded_rate >= 0.95,
        "judge_citation_relevance_rate_ge_90pct": citation_relevance_rate >= 0.90,
        "judge_correct_rate_ge_75pct": correct_rate >= 0.75,
    }
    return {
        "dataset_id": packing_report["dataset_id"],
        "split": packing_report["split"],
        "case_count": len(rows),
        "fixed_context_config": (
            packing_report.get("evaluated_config")
            or packing_report["selection"]["recommended"]
        ),
        "generation_prompt_version": GROUNDED_GENERATION_PROMPT_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "models": {
            "synthesis": synthesis_profile.to_dict(), "judge": judge_profile.to_dict(),
        },
        "metrics": {
            "generator_failure_count": generator_failures,
            "judge_failure_count": judge_failures,
            "abstention_rate": statistics.fmean(float(row["abstained"]) for row in rows),
            "mean_token_f1_vs_reference": statistics.fmean(row["token_f1"] for row in rows),
            "language_match_rate": language_match_rate,
            "gold_evidence_citation_precision_when_available": gold_citation_precision,
            "evidence_citation_recall_when_context_contains_evidence": evidence_citation_recall,
            "judge_grounded_rate": grounded_rate,
            "judge_correct_rate": correct_rate,
            "judge_complete_rate": complete_rate,
            "judge_citation_relevance_rate": citation_relevance_rate,
            "generation_input_tokens": sum(int(row["generation_usage"]["input_tokens"]) for row in rows),
            "generation_output_tokens": sum(int(row["generation_usage"]["output_tokens"]) for row in rows),
            "judge_input_tokens": sum(int(row["judge_usage"]["input_tokens"]) for row in rows),
            "judge_output_tokens": sum(int(row["judge_usage"]["output_tokens"]) for row in rows),
        },
        "deployment_gates": gates,
        "passes_all_gates": all(gates.values()),
        "judge_note": (
            "LLM judgement is secondary and must be calibrated on a human-reviewed subset; "
            "deterministic citation-ID, language, and failure metrics remain authoritative; "
            "gold-evidence citation precision is diagnostic because Doc2Dial annotations are not exhaustive"
        ),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("packing_report", type=Path)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run_generation_evaluation(
        RagDataset.load(args.dataset),
        json.loads(args.packing_report.read_text(encoding="utf-8")),
        concurrency=args.concurrency,
    ))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "case_count": report["case_count"],
        "passes_all_gates": report["passes_all_gates"],
        "metrics": report["metrics"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
