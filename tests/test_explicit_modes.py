"""显式存储和意图相似度模式的边界不变量。"""
import asyncio
from types import SimpleNamespace

import pytest

import core.chroma_client as chroma_module
import core.intent_recognizer as intent_module
import agents.agent_orchestrator as orchestrator_module
from core.intent_recognizer import IntentCategory
from services.evolution import AgentBundle


def test_remote_chroma_failure_does_not_fall_back_to_embedded(monkeypatch):
    persistent_calls = []

    class UnavailableRemote:
        def heartbeat(self):
            raise OSError("connection refused")

    monkeypatch.setattr(chroma_module.chromadb, "HttpClient", lambda **_kwargs: UnavailableRemote())
    monkeypatch.setattr(
        chroma_module.chromadb,
        "PersistentClient",
        lambda **kwargs: persistent_calls.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="CHROMA_MODE=remote"):
        chroma_module.create_chroma_client(
            mode="remote", host="chroma", port=8000, path="/data/chroma",
        )
    assert persistent_calls == []


def test_embedded_chroma_uses_only_declared_path(monkeypatch):
    embedded = object()
    remote_calls = []
    monkeypatch.setattr(
        chroma_module.chromadb,
        "HttpClient",
        lambda **kwargs: remote_calls.append(kwargs),
    )
    monkeypatch.setattr(
        chroma_module.chromadb,
        "PersistentClient",
        lambda **_kwargs: embedded,
    )

    client, backend = chroma_module.create_chroma_client(
        mode="embedded", host="ignored", port=8000, path="/declared/chroma",
    )

    assert client is embedded
    assert backend.to_dict() == {"mode": "embedded", "location": "/declared/chroma"}
    assert remote_calls == []


def test_custom_model_base_url_keeps_explicit_ngram_mode(monkeypatch):
    monkeypatch.setattr(intent_module, "AsyncAnthropic", lambda **_kwargs: SimpleNamespace())
    recognizer = intent_module.IntentRecognizer(
        api_key="test", base_url="https://compatible.example", similarity_mode="ngram",
    )

    intent, score, _sources = recognizer._vote(
        {"intent": IntentCategory.QUERY, "confidence": 1.0},
        {"intent": IntentCategory.QUERY, "confidence": 1.0},
        {"intent": IntentCategory.OTHER, "confidence": 0.0},
    )

    assert recognizer._embedding_enabled is True
    assert intent is IntentCategory.QUERY
    assert score == pytest.approx(0.9)


def test_disabled_similarity_mode_has_explicit_two_way_weights(monkeypatch):
    monkeypatch.setattr(intent_module, "AsyncAnthropic", lambda **_kwargs: SimpleNamespace())
    recognizer = intent_module.IntentRecognizer(api_key="test", similarity_mode="disabled")

    intent, score, _sources = recognizer._vote(
        {"intent": IntentCategory.QUERY, "confidence": 1.0},
        {"intent": IntentCategory.BILLING, "confidence": 1.0},
        {"intent": IntentCategory.QUERY, "confidence": 1.0},
    )

    assert recognizer._embedding_enabled is False
    assert intent is IntentCategory.QUERY
    assert score == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("I have a charge for something I didn't buy.", IntentCategory.ACCOUNT_SECURITY),
        ("What do I need to verify my id?", IntentCategory.ACCOUNT_SECURITY),
        ("My disposable virtual card was rejected.", IntentCategory.TECHNICAL),
        ("How do I reset my PIN?", IntentCategory.TECHNICAL_LOGIN),
        ("Why is there an extra fee on my payment?", IntentCategory.PAYMENT_ISSUE),
    ],
)
def test_business_label_contract_drives_specific_pattern_fallback(
    monkeypatch, message, expected,
):
    """关键歧义样本必须由业务标签合同稳定区分，不能退回宽泛类别。"""
    monkeypatch.setattr(intent_module, "AsyncAnthropic", lambda **_kwargs: SimpleNamespace())
    recognizer = intent_module.IntentRecognizer(api_key="test", similarity_mode="disabled")

    result = recognizer._pattern_recognize(message)

    assert result["intent"] is expected
    assert result["confidence"] >= 0.5


def test_intent_definition_contract_covers_every_supported_enum():
    """新增标签时必须同时声明业务边界，否则 Prompt 与代码会再次分叉。"""
    assert set(intent_module._INTENT_DEFINITIONS) == set(IntentCategory)


def test_intent_classifier_fingerprint_tracks_effective_bundle(monkeypatch):
    """Prompt/Few-shot 变化必须生成新分类器身份，不能继续命中旧缓存。"""
    monkeypatch.setattr(intent_module, "AsyncAnthropic", lambda **_kwargs: SimpleNamespace())
    recognizer = intent_module.IntentRecognizer(api_key="test", similarity_mode="disabled")
    baseline = AgentBundle(version="intent-base")
    candidate = AgentBundle(
        version="intent-candidate",
        base_version="intent-base",
        few_shots={"intent": [{"message": "这笔钱不是我的", "intent": "account_security"}]},
    )

    assert recognizer.classifier_fingerprint(baseline) != recognizer.classifier_fingerprint(candidate)
    assert len(recognizer.classifier_fingerprint(candidate)) == 64


def test_intent_cache_hashes_full_input_and_has_no_online_learn(monkeypatch):
    """相同前缀、不同后缀不能共享结果；线上识别器不再暴露可变 learn。"""
    monkeypatch.setattr(intent_module, "AsyncAnthropic", lambda **_kwargs: SimpleNamespace())
    recognizer = intent_module.IntentRecognizer(api_key="test", similarity_mode="disabled")
    calls = []

    async def classify(message, _history, bundle=None):
        calls.append((message, bundle))
        return {"intent": IntentCategory.QUERY, "confidence": 1.0, "reasoning": "fixture"}

    recognizer._llm_recognize = classify
    prefix = "x" * 220
    first = asyncio.run(recognizer.recognize(prefix + "退款"))
    second = asyncio.run(recognizer.recognize(prefix + "非本人交易"))
    cached = asyncio.run(recognizer.recognize(prefix + "退款"))

    assert len(calls) == 2
    assert first.input_fingerprint != second.input_fingerprint
    assert cached.input_fingerprint == first.input_fingerprint
    assert recognizer.cache_stats["hits"] == 1
    assert not hasattr(recognizer, "learn")
    with pytest.raises(TypeError):
        intent_module._TEMPLATES[IntentCategory.REFUND] = ("现场修改",)


def test_unknown_modes_fail_with_typed_configuration_errors(monkeypatch):
    monkeypatch.setattr(intent_module, "AsyncAnthropic", lambda **_kwargs: SimpleNamespace())
    with pytest.raises(ValueError, match="INTENT_SIMILARITY_MODE"):
        intent_module.IntentRecognizer(api_key="test", similarity_mode="magic")
    with pytest.raises(ValueError, match="CHROMA_MODE"):
        chroma_module.create_chroma_client(
            mode="magic", host="ignored", port=8000, path="ignored",
        )


def test_real_orchestrator_constructor_accepts_and_forwards_similarity_mode(monkeypatch):
    """覆盖真实构造签名，防止生命周期假对象掩盖装配错误。"""
    captured = {}

    class FakeRecognizer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeAnthropic:
        def __init__(self, **_kwargs):
            self.messages = self

    monkeypatch.setattr(orchestrator_module, "IntentRecognizer", FakeRecognizer)
    monkeypatch.setattr(orchestrator_module, "AsyncAnthropic", FakeAnthropic)

    orchestrator = orchestrator_module.AgentOrchestrator(
        api_key="test", intent_similarity_mode="disabled",
    )

    assert captured["similarity_mode"] == "disabled"
    assert orchestrator._best_agent(orchestrator_module.AgentType.ESCALATION).instance_id == "escalation_0"
