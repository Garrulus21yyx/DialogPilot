#!/usr/bin/env python3
"""Build reviewed Chinese intent-training augmentation from labeled public data."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.model_policy import ModelPolicy, ModelRole  # noqa: E402


KNOWN_LABELS = (
    "account",
    "account_security",
    "logistics",
    "payment_issue",
    "query",
    "refund",
    "technical",
    "technical_login",
)
LABEL_CONTRACT = {
    "account": "维护或关闭本项目银行账户资料，不含安全事件",
    "account_security": "非本人交易、卡或手机丢失、账户泄露、未主动请求的验证码等安全事件",
    "logistics": "本项目银行卡寄送后的运输位置、到达时间",
    "payment_issue": "本人发起的支付失败、被拒、重复扣款、额外支付费用",
    "query": "本项目支持国家、币种、ATM、汇率、卡组织或银行卡受理范围等产品信息",
    "refund": "取消购买、退货返款、退款未到账",
    "technical": "银行卡本体、非接触支付、虚拟卡功能不可用",
    "technical_login": "本人忘记密码或 PIN、PIN 锁定、登录验证码故障",
    "other": "意思明确但超出本项目银行/银行卡范围，或脱离上下文无法确定业务指代",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "data/eval/intent-weight-calibration-2026-08-31/cases.jsonl",
    )
    parser.add_argument(
        "--diagnostic",
        type=Path,
        default=ROOT / "fresh-intent-candidate.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/training/intent-chinese-augmentation-2026-08-31",
    )
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model response contains no JSON object")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model response must be a JSON object")
    return payload


def normalize_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text).lower(), flags=re.UNICODE)


def validate_batch(payload: dict[str, Any], expected_ids: set[str]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("model response items must be a list")
    indexed = {str(item.get("id")): item for item in items if isinstance(item, dict)}
    if set(indexed) != expected_ids:
        raise ValueError(
            f"model response id mismatch: missing={sorted(expected_ids - set(indexed))}, "
            f"extra={sorted(set(indexed) - expected_ids)}"
        )
    result = []
    for case_id in sorted(expected_ids):
        item = indexed[case_id]
        text = str(item.get("text") or "").strip()
        if not text or not re.search(r"[\u4e00-\u9fff]", text):
            raise ValueError(f"{case_id} does not contain a Chinese text")
        result.append({**item, "id": case_id, "text": text})
    return result


class BatchGenerator:
    def __init__(
        self,
        client: AsyncAnthropic,
        policy: ModelPolicy,
        *,
        concurrency: int,
    ) -> None:
        self.client = client
        self.rewrite = policy.profile(ModelRole.REWRITE)
        self.verifier = policy.profile(ModelRole.VERIFIER)
        self.semaphore = asyncio.Semaphore(max(1, concurrency))

    async def request(
        self,
        *,
        profile: Any,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.semaphore:
                    response = await self.client.messages.create(
                        **profile.request(
                            max_tokens=max_tokens,
                            temperature=temperature,
                            messages=[{"role": "user", "content": prompt}],
                        )
                    )
                text = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                return extract_json_object(text)
            except Exception as exc:  # network/model JSON failures are retryable here
                last_error = exc
                await asyncio.sleep(2**attempt)
        raise RuntimeError(f"model request failed after retries: {last_error}")

    async def translate(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact = [
            {
                "id": row["id"],
                "label": row["expected"]["intent"],
                "source_text": row["input"]["message"],
            }
            for row in batch
        ]
        prompt = f"""你是客服意图训练数据翻译器。把每条英文用户话语改写成自然、真实、简短的中文客服表达。
必须逐条保持原意、说话主体、本人/非本人、否定、时间和问题阶段；不得添加原文没有的事实，不得把域外问题改成银行问题。
标签只帮助消歧，不得出现在输出文本中。标签合同：{json.dumps(LABEL_CONTRACT, ensure_ascii=False)}
只输出严格 JSON：{{"items":[{{"id":"原id","text":"中文"}}]}}，id 和数量必须完全一致。
输入：{json.dumps(compact, ensure_ascii=False)}"""
        payload = await self.request(
            profile=self.rewrite,
            prompt=prompt,
            max_tokens=4096,
            temperature=0.35,
        )
        return validate_batch(payload, {row["id"] for row in batch})

    async def generate_insufficient(self, ids: list[str]) -> list[dict[str, Any]]:
        prompt = f"""生成一组自然中文客服消息。每条消息在没有任何对话历史时都信息不足：没有可解析的银行业务对象、动作或指代，系统必须追问，不能归入具体意图。
覆盖省略、模糊指代、只说结果没变化、含糊抱怨、无对象问句；彼此措辞不同。不要出现银行卡、付款、退款、账户、登录、验证码、物流等能直接确定意图的词。
只输出严格 JSON：{{"items":[{{"id":"指定id","text":"消息"}}]}}。
指定 id：{json.dumps(ids, ensure_ascii=False)}"""
        payload = await self.request(
            profile=self.rewrite,
            prompt=prompt,
            max_tokens=2048,
            temperature=0.7,
        )
        return validate_batch(payload, set(ids))

    async def review(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact = [
            {
                "id": row["id"],
                "label": row["label"],
                "source_text": row.get("source_text"),
                "candidate_text": row["text"],
                "kind": row["kind"],
            }
            for row in batch
        ]
        prompt = f"""你是独立意图数据审查员。标签合同：{json.dumps(LABEL_CONTRACT, ensure_ascii=False)}
逐条检查中文候选是否自然、标签正确。translation 必须忠实保持英文原文的主体、否定、本人/非本人和业务阶段；insufficient 必须在无历史时确实无法判断具体业务。
若候选有任何问题，直接在 text 中给出修正后的合格中文；accepted 表示原候选是否无需修改。修正也不得改变英文源意或目标标签。
只输出严格 JSON：{{"items":[{{"id":"原id","accepted":true,"text":"最终合格中文","reason":"简短审查理由"}}]}}。
输入：{json.dumps(compact, ensure_ascii=False)}"""
        payload = await self.request(
            profile=self.verifier,
            prompt=prompt,
            max_tokens=6144,
            temperature=0.0,
        )
        reviewed = validate_batch(payload, {row["id"] for row in batch})
        for item in reviewed:
            item["accepted"] = bool(item.get("accepted", False))
            item["reason"] = str(item.get("reason") or "").strip()
        return reviewed

    async def deduplicate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact = [
            {
                "id": row["id"],
                "label": row["label"],
                "source_text": row.get("source_text"),
                "current_text": row["text"],
            }
            for row in rows
        ]
        prompt = f"""以下中文训练文本发生完全重复。请根据各自英文源意改写成彼此不同、自然简短的中文，同时严格保持标签、主体、否定和业务阶段。不得靠添加虚构事实制造差异。
标签合同：{json.dumps(LABEL_CONTRACT, ensure_ascii=False)}
只输出严格 JSON：{{"items":[{{"id":"原id","text":"去重后的中文"}}]}}，所有 text 规范化后必须互不相同。
输入：{json.dumps(compact, ensure_ascii=False)}"""
        payload = await self.request(
            profile=self.verifier,
            prompt=prompt,
            max_tokens=2048,
            temperature=0.3,
        )
        return validate_batch(payload, {row["id"] for row in rows})


def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


async def generate_missing(
    generator: BatchGenerator,
    sources: list[dict[str, Any]],
    output: Path,
    batch_size: int,
) -> dict[str, dict[str, Any]]:
    existing = {row["id"]: row for row in read_jsonl(output)} if output.exists() else {}
    pending = [row for row in sources if row["id"] not in existing]

    async def run(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        translated = await generator.translate(batch)
        source_by_id = {row["id"]: row for row in batch}
        result = []
        for item in translated:
            source = source_by_id[item["id"]]
            result.append(
                {
                    "id": item["id"],
                    "label": source["expected"]["intent"],
                    "source_text": source["input"]["message"],
                    "source_case_id": source["id"],
                    "source_dataset": source["source"]["dataset"],
                    "source_original_label": source["source"].get("original_label"),
                    "text": item["text"],
                    "kind": "translation",
                }
            )
        append_jsonl(output, result)
        print(f"translated {len(existing) + len(result)}/{len(sources)}", flush=True)
        return result

    generated = await asyncio.gather(*(run(batch) for batch in batches(pending, batch_size)))
    for rows in generated:
        existing.update({row["id"]: row for row in rows})
    return existing


async def review_missing(
    generator: BatchGenerator,
    candidates: list[dict[str, Any]],
    output: Path,
    batch_size: int,
) -> dict[str, dict[str, Any]]:
    existing = {row["id"]: row for row in read_jsonl(output)} if output.exists() else {}
    pending = [row for row in candidates if row["id"] not in existing]

    async def run(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reviewed = await generator.review(batch)
        candidate_by_id = {row["id"]: row for row in batch}
        result = []
        for item in reviewed:
            source = candidate_by_id[item["id"]]
            result.append(
                {
                    **source,
                    "candidate_text": source["text"],
                    "text": item["text"],
                    "accepted": item["accepted"],
                    "review_reason": item["reason"],
                }
            )
        append_jsonl(output, result)
        print(f"reviewed {len(existing) + len(result)}/{len(candidates)}", flush=True)
        return result

    generated = await asyncio.gather(*(run(batch) for batch in batches(pending, batch_size)))
    for rows in generated:
        existing.update({row["id"]: row for row in rows})
    return existing


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    policy = ModelPolicy.from_env()
    client = AsyncAnthropic(api_key=key, base_url=policy.base_url)
    generator = BatchGenerator(client, policy, concurrency=args.concurrency)

    source_rows = [
        row
        for row in read_jsonl(args.source)
        if row.get("split") == "dev" and row["expected"]["intent"] in {*KNOWN_LABELS, "other"}
    ]
    selected = []
    for label in KNOWN_LABELS:
        rows = sorted(
            (row for row in source_rows if row["expected"]["intent"] == label),
            key=lambda row: row["id"],
        )
        if len(rows) < 50:
            raise RuntimeError(f"{label} has fewer than 50 source rows")
        selected.extend(rows[:50])
    other_rows = sorted(
        (row for row in source_rows if row["expected"]["intent"] == "other"),
        key=lambda row: row["id"],
    )[:75]
    selected.extend(other_rows)

    args.output.mkdir(parents=True, exist_ok=True)
    translations_path = args.output / "translation-candidates.jsonl"
    translated = await generate_missing(
        generator, selected, translations_path, max(1, args.batch_size)
    )
    insufficient_ids = [f"zh-insufficient-{index:03d}" for index in range(1, 26)]
    insufficient_path = args.output / "insufficient-candidates.jsonl"
    insufficient_existing = (
        {row["id"]: row for row in read_jsonl(insufficient_path)}
        if insufficient_path.exists()
        else {}
    )
    if set(insufficient_ids) - set(insufficient_existing):
        generated = await generator.generate_insufficient(insufficient_ids)
        rows = [
            {
                "id": item["id"],
                "label": "other",
                "source_text": None,
                "source_case_id": None,
                "source_dataset": "llm-generated-insufficient-context",
                "source_original_label": "insufficient_context",
                "text": item["text"],
                "kind": "insufficient",
            }
            for item in generated
        ]
        append_jsonl(insufficient_path, rows)
        insufficient_existing.update({row["id"]: row for row in rows})

    candidates = sorted(
        [translated[row["id"]] for row in selected]
        + [insufficient_existing[case_id] for case_id in insufficient_ids],
        key=lambda row: row["id"],
    )
    reviews_path = args.output / "reviews.jsonl"
    reviewed = await review_missing(
        generator, candidates, reviews_path, max(1, args.batch_size)
    )

    correction_path = args.output / "dedup-corrections.jsonl"
    corrections = (
        {row["id"]: row for row in read_jsonl(correction_path)}
        if correction_path.exists()
        else {}
    )
    for case_id, correction in corrections.items():
        if case_id in reviewed:
            reviewed[case_id]["text"] = correction["text"]
            reviewed[case_id]["review_reason"] += "; duplicate repaired by reviewer"
    duplicate_groups: dict[str, list[dict[str, Any]]] = {}
    for item in reviewed.values():
        duplicate_groups.setdefault(normalize_text(item["text"]), []).append(item)
    conflicts = [rows for rows in duplicate_groups.values() if len(rows) > 1]
    if conflicts:
        repaired = await generator.deduplicate([item for rows in conflicts for item in rows])
        repair_rows = [
            {
                "id": item["id"],
                "text": item["text"],
                "reason": "duplicate repaired by independent reviewer",
            }
            for item in repaired
        ]
        append_jsonl(correction_path, repair_rows)
        for item in repair_rows:
            reviewed[item["id"]]["text"] = item["text"]
            reviewed[item["id"]]["review_reason"] += "; duplicate repaired by reviewer"

    diagnostic_normalized = {
        normalize_text(row["input"]["message"])
        for row in read_jsonl(args.diagnostic)
    }
    final_rows = []
    seen: dict[str, str] = {}
    for item in sorted(reviewed.values(), key=lambda row: row["id"]):
        normalized = normalize_text(item["text"])
        if not normalized:
            raise RuntimeError(f"empty normalized text: {item['id']}")
        if normalized in diagnostic_normalized:
            raise RuntimeError(f"exact diagnostic leakage: {item['id']}")
        if normalized in seen:
            raise RuntimeError(f"duplicate final text: {seen[normalized]} and {item['id']}")
        seen[normalized] = item["id"]
        final_rows.append(
            {
                "schema_version": 1,
                "id": item["id"],
                "group_id": item["source_case_id"] or item["id"],
                "layer": "intent",
                "split": "train",
                "input": {"message": item["text"], "history": []},
                "expected": {"intent": item["label"]},
                "review": {
                    "status": (
                        "independently_reviewed_synthetic"
                        if item["accepted"]
                        else "independently_corrected_synthetic"
                    ),
                    "reviewer": generator.verifier.model,
                    "notes": item["review_reason"],
                },
                "source": {
                    "dataset": "dialogpilot-chinese-intent-augmentation-v1",
                    "kind": item["kind"],
                    "generator_model": generator.rewrite.model,
                    "reviewer_model": generator.verifier.model,
                    "source_dataset": item["source_dataset"],
                    "source_case_id": item["source_case_id"],
                    "source_original_label": item["source_original_label"],
                    "license": "synthetic-derivative; see upstream source metadata",
                },
                "tags": ["synthetic", "llm-reviewed", "chinese-training-augmentation"],
            }
        )

    cases_path = args.output / "cases.jsonl"
    cases_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in final_rows),
        encoding="utf-8",
    )
    counts = Counter(row["expected"]["intent"] for row in final_rows)
    if any(counts[label] < 50 or counts[label] > 100 for label in (*KNOWN_LABELS, "other")):
        raise RuntimeError(f"label counts outside 50..100: {dict(counts)}")
    manifest = {
        "schema_version": 1,
        "status": "llm_reviewed_synthetic_training_only",
        "case_count": len(final_rows),
        "label_counts": dict(sorted(counts.items())),
        "generator": generator.rewrite.to_dict(),
        "reviewer": generator.verifier.to_dict(),
        "source_sha256": sha256(args.source),
        "diagnostic_exclusion_sha256": sha256(args.diagnostic),
        "cases_sha256": sha256(cases_path),
        "original_candidates_accepted": sum(bool(row["accepted"]) for row in reviewed.values()),
        "reviewer_corrections": sum(not bool(row["accepted"]) for row in reviewed.values()),
        "limitations": [
            "No human gold review was available.",
            "Translations inherit the upstream label mapping.",
            "The existing Chinese diagnostic set was excluded by construction and exact-text check.",
        ],
    }
    write_json(args.output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
