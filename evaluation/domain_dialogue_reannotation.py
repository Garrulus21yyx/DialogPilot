"""Versioned, label-blind reannotation of the entire structurally valid source."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from pydantic import BaseModel, Field

from dotenv import dotenv_values

from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from evaluation.domain_dialogue_teacher import (
    AuthoredBatch, ReviewedBatch, Verdict, Label, RUBRIC_VERSION, blind_cases, structured_call,
)

ANNOTATION_VERSION = "active-domain-set-v1"


class DomainSetVerdict(BaseModel):
    case_id: str
    active_domains: list[Label] = Field(min_length=1, max_length=7)
    clear_and_natural: bool
    reason: str


class DomainSetReview(BaseModel):
    verdicts: list[DomainSetVerdict]

    def labels(self):
        # The model identifies services; the dataset owner composes the label.
        return ReviewedBatch(verdicts=[Verdict(case_id=v.case_id,
            label=next(iter(set(v.active_domains))) if len(set(v.active_domains)) == 1 else "__DEFER__",
            clear_and_natural=v.clear_and_natural, reason=v.reason) for v in self.verdicts])


def reannotated_rows(cases, review, language):
    verdicts = {v.case_id: v for v in review.verdicts}
    if len(verdicts) != len(review.verdicts) or set(verdicts) != {c["case_id"] for c in cases}:
        raise ValueError("review_coverage_mismatch")
    return [{**case, "language": language, "label": verdicts[case["case_id"]].label,
             "group_id": case["case_id"].rsplit(":", 1)[0],
             "source": f"synthetic-reannotation:{RUBRIC_VERSION}"}
            for case in cases if verdicts[case["case_id"]].clear_and_natural]


async def run(source: Path, output: Path):
    env = dotenv_values(".env")
    policy = ModelPolicy.from_env(env)
    profile = policy.profile(ModelRole.JUDGE)
    model = framework_model(profile, {"api_key": env["ANTHROPIC_API_KEY"],
        "base_url": policy.base_url}, max_tokens=8192)
    semaphore = asyncio.Semaphore(4)

    async def one(path):
        original = json.loads(path.read_text())
        if original["status"] != "reviewed":
            return
        destination = output / path.relative_to(source)
        if destination.exists():
            raise ValueError("reannotation destination exists; original results are immutable")
        cases = blind_cases(AuthoredBatch.model_validate(original["authored"]), original["batch_id"])
        record = {"batch_id": original["batch_id"], "language": original["language"],
                  "split": original["split"], "source": str(path),
                  "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "rubric_version": RUBRIC_VERSION, "annotation_version": ANNOTATION_VERSION,
                  "model": profile.model}
        async with semaphore:
            try:
                annotation, record["call"] = await structured_call(model, DomainSetReview,
                    "For each current turn list ALL active domains of separately requested services. "
                    "There is NO primary/secondary ranking: keep every requested domain. "
                    "Do not output one final label; code will combine the set. Use [__DEFER__] for "
                    "withdrawal/unresolved requests and [general] for acknowledgement with no work. "
                    "A received damaged item is after-sales, not undelivered-parcel recovery. "
                    "Return every case exactly once.\n"
                    + json.dumps(cases, ensure_ascii=False), "domain_data_reannotation")
                if annotation is None:
                    record.update(status="failed", error_code="review_schema_invalid")
                else:
                    record["annotation"] = annotation.model_dump()
                    review = annotation.labels()
                    record["review"] = review.model_dump()
                    record["rows"] = reannotated_rows(cases, review, original["language"])
                    record["status"] = "reviewed_candidate"
            except Exception as exc:
                record.update(status="failed", error_type=type(exc).__name__)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"batch": record["batch_id"], "status": record["status"],
                              "rows": len(record.get("rows", []))}), flush=True)

    paths = sorted(source.glob("*/batch-*.json"))
    if len(paths) != 48:
        raise ValueError("expected complete registered source batch set")
    await asyncio.gather(*(one(path) for path in paths))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(**vars(parser.parse_args())))
