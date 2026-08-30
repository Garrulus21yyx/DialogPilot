"""显式存储和意图相似度模式的边界不变量。"""
from types import SimpleNamespace

import pytest

import core.chroma_client as chroma_module
import core.intent_recognizer as intent_module
import agents.agent_orchestrator as orchestrator_module
from core.intent_recognizer import IntentCategory


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
