"""Offline free-dialogue authoring and blind review through the existing model SDK.

Synthetic data only. No benchmark examples, future turns or production changes.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator

from application.domain_encoder import DOMAINS
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole

Label = Literal["general", "product_technical", "order_logistics", "billing_refund",
                "account_security", "human_service", "__DEFER__"]

RUBRIC = """Classify ONLY the currently effective customer request using its prior dialogue.
general: greetings, thanks, ordinary chat, or general help/capabilities.
product_technical: products, specifications, compatibility, usage, troubleshooting.
order_logistics: order/shipping/delivery/address, cancellation before dispatch.
billing_refund: payment/invoice/refund/return/exchange and after-sales rules.
account_security: login/account access/security.
human_service: explicit human service/escalation/complaint ticket or ticket status.
__DEFER__: unresolved referent, withdrawal with no remaining goal, or simultaneous
requests in different domains. Explicit replacement discards the old goal. Two
requests in ONE domain remain that domain. Courtesy attached to a business request
is not an extra goal. A completed old request does not remain active forever.
Do not infer a goal from isolated keywords or provide tool/parameter/action labels.
There is no pending formal approval/control signal in these examples.
"""


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class Variant(BaseModel):
    text: str = Field(min_length=1)
    label: Label


class DialogueFamily(BaseModel):
    scenario: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=2, max_length=6)
    variants: list[Variant] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def conversational_prefix(self):
        if len(self.messages) % 2 or any(
            m.role != ("user" if i % 2 == 0 else "assistant")
            for i, m in enumerate(self.messages)
        ):
            raise ValueError("history must alternate and end before the current user turn")
        if len({v.text.strip() for v in self.variants}) != 3:
            raise ValueError("paired turns must differ")
        if len({v.label for v in self.variants}) < 2:
            raise ValueError("paired turns must expose a semantic domain boundary")
        return self


class AuthoredBatch(BaseModel):
    families: list[DialogueFamily] = Field(min_length=8, max_length=8)


class Verdict(BaseModel):
    case_id: str
    label: Label
    clear_and_natural: bool
    reason: str


class ReviewedBatch(BaseModel):
    verdicts: list[Verdict]


def blind_cases(batch: AuthoredBatch, batch_id: str) -> list[dict]:
    return [{"case_id": f"{batch_id}:{i}:{j}",
             "messages": [m.model_dump() for m in family.messages], "text": variant.text}
            for i, family in enumerate(batch.families) for j, variant in enumerate(family.variants)]


def accepted_rows(batch: AuthoredBatch, review: ReviewedBatch, batch_id: str, language: str):
    cases = blind_cases(batch, batch_id)
    verdicts = {v.case_id: v for v in review.verdicts}
    if len(verdicts) != len(review.verdicts) or set(verdicts) != {c["case_id"] for c in cases}:
        raise ValueError("review must cover each case exactly once")
    rows, rejected = [], []
    for case in cases:
        i, j = map(int, case["case_id"].split(":")[-2:])
        original = batch.families[i].variants[j]
        verdict = verdicts[case["case_id"]]
        if verdict.label != original.label or not verdict.clear_and_natural:
            rejected.append({"case_id": case["case_id"], "author": original.label,
                             **verdict.model_dump(exclude={"case_id"})})
        else:
            rows.append({**case, "label": verdict.label, "language": language,
                         "group_id": f"{batch_id}:{i}", "source": "blind-reviewed-synthetic-dialogue-v3"})
    return rows, rejected


async def structured_call(model, schema, prompt, name):
    # Framework owns tool arguments, JSON parsing and schema validation.
    chain = model.with_structured_output(schema, include_raw=True)
    result = await asyncio.wait_for(chain.ainvoke([
        SystemMessage(content=RUBRIC), HumanMessage(content=prompt)
    ], config={"run_name": name}), timeout=180)
    raw = result["raw"]
    record = {"response": raw.model_dump(mode="json"),
              "error": str(result["parsing_error"]) if result["parsing_error"] else None}
    return result["parsed"], record


def author_prompt(language, batch):
    focus = DOMAINS[batch % len(DOMAINS)]
    return f"""Author 8 DIFFERENT natural ecommerce customer-service dialogue families in {language}.
Batch {batch}; ensure at least four current turns classified as {focus} across this batch.
Each family has its OWN coherent history of 2, 4 or 6 short alternating user/assistant
messages, followed by 3 ALTERNATIVE current user turns, not three sequential turns.
Those alternatives differ in effective intent: retain vs replace, withdraw vs
continue, single goal vs concurrent goal, or resolved vs unresolved reference.
Include natural short answers where history matters. Vary old completed tasks,
hesitation, self-correction, indirect wording, topics and dialogue acts. General
and human-service requests must also occur as NEW goals, not only stale history.
Do not follow a repeated sentence template or substitute product nouns to create
new families. Avoid stereotyped 'information only, do not submit' repetition.
Invent broad everyday ecommerce situations; no copied benchmark or private data.
History plus current turn should fit 150 English words or 180 Chinese characters;
keep it compact but meaningful. No future replies or final conversation summaries.
Supply the correct label for each alternative according to the rubric.
"""


async def generate(output: Path, start: int, stop: int, languages: list[str]):
    if not 0 <= start < stop <= 24:
        raise ValueError("registered batch range is 0..23")
    env = dotenv_values(".env")
    policy = ModelPolicy.from_env(env)
    author_profile = policy.profile(ModelRole.WORKER)
    review_profile = policy.profile(ModelRole.JUDGE)
    provider = {"api_key": env["ANTHROPIC_API_KEY"], "base_url": policy.base_url}
    author_model = framework_model(author_profile, provider, max_tokens=8192)
    review_model = framework_model(review_profile, provider, max_tokens=8192)
    semaphore = asyncio.Semaphore(4)

    async def one(language, index):
        batch_id = f"domain-v3:{language}:{index}"
        destination = output / language / f"batch-{index:02}.json"
        if destination.exists():
            print(json.dumps({"batch": batch_id, "status": "existing_not_retried"}), flush=True)
            return
        async with semaphore:
            record = {"batch_id": batch_id, "language": language,
                      "split": "train" if index < 16 else "calibration" if index < 20 else "heldout",
                      "models": {"author": author_profile.model, "review": review_profile.model},
                      "review_protocol": "label-blind-batch-v1", "synthetic": True}
            stage = "author"
            try:
                prompt = author_prompt(language, index)
                record["author_prompt"] = prompt
                authored, record["author_call"] = await structured_call(author_model, AuthoredBatch, prompt, "domain_data_author")
                if authored is None:
                    raise ValueError("author_schema_invalid")
                record["authored"] = authored.model_dump()
                stage = "review"
                prompt = ("Independently label each current turn. You have only its prior history. "
                          "Mark ambiguous/unnatural cases clear_and_natural=false. Return one verdict per case.\n"
                          + json.dumps(blind_cases(authored, batch_id), ensure_ascii=False))
                record["review_input_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
                reviewed, record["review_call"] = await structured_call(review_model, ReviewedBatch, prompt, "domain_data_blind_review")
                if reviewed is None:
                    raise ValueError("review_schema_invalid")
                record["reviewed"] = reviewed.model_dump()
                record["rows"], record["rejected"] = accepted_rows(authored, reviewed, batch_id, language)
                record["status"] = "reviewed"
            except Exception as exc:
                # Preserve failure class/stage, never provider credentials/error payloads.
                record.update(status="failed", failure_stage=stage, error_type=type(exc).__name__)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"batch": batch_id, "status": record["status"],
                              "accepted": len(record.get("rows", [])),
                              "error_type": record.get("error_type")}), flush=True)

    await asyncio.gather(*(one(lang, index) for lang in languages for index in range(start, stop)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int, default=24)
    parser.add_argument("--languages", nargs="+", choices=("zh", "en"), default=["zh", "en"])
    asyncio.run(generate(**vars(parser.parse_args())))
