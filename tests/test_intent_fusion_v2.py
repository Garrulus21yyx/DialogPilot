import asyncio
import math

import pytest

from core.intent_fusion_v2 import (
    BGESemanticIntentProvider,
    FusionPolicyV2,
    FusionStatus,
    SemanticCandidate,
    SemanticEvidence,
    extract_pattern_evidence,
    pattern_supports,
    validate_semantic_prototypes,
)
from core.intent_recognizer import EvidencePolarity, IntentCategory, _SEMANTIC_PROTOTYPES


class PrototypeEncoder:
    def __init__(self):
        intents = list(_SEMANTIC_PROTOTYPES)
        self.index = {intent: index for index, intent in enumerate(intents)}
        self.by_text = {
            text: self._basis(intent)
            for intent, examples in _SEMANTIC_PROTOTYPES.items()
            for text in examples
        }

    def _basis(self, intent):
        vector = [0.0] * len(self.index)
        vector[self.index[intent]] = 1.0
        return vector

    def encode(self, texts):
        vectors = []
        for text in texts:
            if text == "strong refund":
                vectors.append(self._basis(IntentCategory.REFUND))
            elif text == "ambiguous":
                vector = [0.0] * len(self.index)
                vector[self.index[IntentCategory.REFUND]] = 1 / math.sqrt(2)
                vector[self.index[IntentCategory.INVOICE]] = 1 / math.sqrt(2)
                vectors.append(vector)
            else:
                vectors.append(self.by_text[text])
        return vectors


def semantic(intent, score=0.9, *, accepted=True, second=IntentCategory.BILLING):
    return SemanticEvidence(
        provider="test",
        top1=SemanticCandidate(intent, score),
        top2=SemanticCandidate(second, score - 0.2),
        margin=0.2,
        threshold=0.7,
        min_margin=0.05,
        accepted=accepted,
    )


def test_semantic_prototypes_form_a_closed_unique_owner_contract():
    validate_semantic_prototypes()
    normalized = [
        "".join(text.lower().split()).strip("，。！？,.!?")
        for examples in _SEMANTIC_PROTOTYPES.values()
        for text in examples
    ]
    assert len(normalized) == len(set(normalized))
    assert set(_SEMANTIC_PROTOTYPES) == set(IntentCategory) - {IntentCategory.OTHER}


def test_semantic_provider_requires_threshold_and_margin():
    provider = BGESemanticIntentProvider(
        PrototypeEncoder(), default_threshold=0.7, min_margin=0.1,
    )
    accepted = asyncio.run(provider.recognize("strong refund"))
    rejected = asyncio.run(provider.recognize("ambiguous"))
    assert accepted.accepted is True
    assert accepted.top1.intent is IntentCategory.REFUND
    assert rejected.accepted is False
    assert rejected.top1.score >= rejected.threshold
    assert rejected.margin == pytest.approx(0.0)


def test_pattern_evidence_exposes_negative_and_quoted_spans():
    evidence = extract_pattern_evidence("不是退款问题，客服说“发票”，只是登录报 401")
    refund = [item for item in evidence if item.intent is IntentCategory.REFUND]
    invoice = [item for item in evidence if item.intent is IntentCategory.INVOICE]
    login = [item for item in evidence if item.intent is IntentCategory.TECHNICAL_LOGIN]
    assert refund and all(item.polarity is EvidencePolarity.NEGATIVE for item in refund)
    assert invoice and all(item.polarity is EvidencePolarity.QUOTED for item in invoice)
    assert login and any(item.polarity is EvidencePolarity.POSITIVE for item in login)
    assert all(item.span for item in evidence)
    assert pattern_supports(evidence, IntentCategory.REFUND) is False
    assert pattern_supports(evidence, IntentCategory.TECHNICAL_LOGIN) is True


def test_v2_preserves_accepted_specific_llm_when_auxiliaries_disagree():
    policy = FusionPolicyV2()
    pattern = extract_pattern_evidence("登录报 401 而且重复扣款")
    decision = policy.decide(
        {"intent": IntentCategory.TECHNICAL_LOGIN, "confidence": 0.6},
        semantic(IntentCategory.PAYMENT_ISSUE),
        pattern,
    )
    assert decision.status is FusionStatus.CLASSIFIED
    assert decision.intent is IntentCategory.TECHNICAL_LOGIN
    assert decision.reason == "accepted_specific_llm_authority"


def test_v2_refines_only_legal_parent_child_with_positive_agreement():
    policy = FusionPolicyV2()
    legal = policy.decide(
        {"intent": IntentCategory.BILLING, "confidence": 0.8},
        semantic(IntentCategory.REFUND),
        extract_pattern_evidence("我要退款"),
    )
    illegal = policy.decide(
        {"intent": IntentCategory.BILLING, "confidence": 0.8},
        semantic(IntentCategory.TECHNICAL_LOGIN),
        extract_pattern_evidence("无法登录"),
    )
    negated = policy.decide(
        {"intent": IntentCategory.BILLING, "confidence": 0.8},
        semantic(IntentCategory.REFUND),
        extract_pattern_evidence("不是退款问题，只想看账单"),
    )
    assert legal.intent is IntentCategory.REFUND
    assert legal.reason == "legal_parent_child_refinement"
    assert illegal.intent is IntentCategory.BILLING
    assert negated.intent is IntentCategory.BILLING


def test_v2_returns_typed_provider_failure_without_accepted_fallback():
    policy = FusionPolicyV2()
    decision = policy.decide(
        {"intent": IntentCategory.OTHER, "confidence": 0.0, "failed": True},
        SemanticEvidence.failure("test", "Unavailable"),
        (),
    )
    assert decision.status is FusionStatus.PROVIDER_FAILURE
    assert decision.intent is IntentCategory.OTHER
