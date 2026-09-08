"""Domain routing contract: select a specialist, never an executable action."""
from dataclasses import dataclass
from typing import Mapping

from application.encoder_fast_path import RankedCandidate
from application.encoder_input import CONTEXT_INPUT_SCHEMA

DOMAIN_ENCODER_SCHEMA = "dialogpilot-domain-encoder-v1"
DOMAINS = ("general", "product_technical", "order_logistics", "billing_refund",
           "account_security", "human_service")
DEFER_DOMAIN = "__DEFER__"
DOMAIN_MINIMUM_MARGIN = .08


class DomainEncoderUnavailable(RuntimeError):
    """Inference dependency failure; distinct from an uncertain classification."""


@dataclass(frozen=True)
class DomainEncoderManifest:
    language: str
    thresholds: Mapping[str, float]
    model_sha256: str
    schema_version: str = DOMAIN_ENCODER_SCHEMA
    input_schema: str = CONTEXT_INPUT_SCHEMA
    status: str = "CANDIDATE"

    @classmethod
    def from_dict(cls, value, *, evaluation=False):
        if value.get("schema_version") != DOMAIN_ENCODER_SCHEMA:
            raise ValueError("not a domain encoder artifact; legacy action artifacts are unsupported")
        result = cls(**{key: value[key] for key in cls.__dataclass_fields__})
        allowed_statuses = {"ACTIVE", "CANDIDATE", "REJECTED"} if evaluation else {"ACTIVE"}
        if (result.schema_version != DOMAIN_ENCODER_SCHEMA
                or result.input_schema != CONTEXT_INPUT_SCHEMA or result.status not in allowed_statuses):
            raise ValueError("not an active domain encoder artifact")
        if result.language not in {"zh", "en"} or not result.thresholds:
            raise ValueError("domain encoder requires language and calibrated domains")
        if not set(result.thresholds) <= set(DOMAINS) or any(
                not 0 <= threshold <= 1 for threshold in result.thresholds.values()):
            raise ValueError("invalid domain thresholds")
        # The release cannot enable classes that failed its recorded quality gate.
        for domain in result.thresholds:
            metrics = value["calibration"][domain]
            heldout = value["development"][domain]
            if (metrics["accepted"] < 10 or heldout["accepted"] < 10
                    or metrics["correct"] / metrics["accepted"] < .98
                    or heldout["correct"] / heldout["accepted"] < .98):
                raise ValueError("domain class failed acceptance gate")
        return result


@dataclass(frozen=True)
class DomainPrediction:
    candidates: tuple[RankedCandidate, ...]
    defer_score: float

    def __post_init__(self):
        labels = [r.candidate_id for r in self.candidates]
        if not labels or len(labels) != len(set(labels)) or not set(labels) <= set(DOMAINS):
            raise ValueError("invalid domain candidates")
        if not 0 <= self.defer_score <= 1:
            raise ValueError("invalid domain defer score")
