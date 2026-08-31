"""Bad Case 去重、隐私、状态机、线上捕获与 regression 导出合同。"""
import asyncio
import hashlib
import hmac
from types import SimpleNamespace

import pytest

from api import main
from core.auth import Principal
from scripts.promote_badcase import export_badcases
from services.answer_verifier import (
    VerificationReasonCode,
    VerificationResult,
    VerificationStatus,
)
from services.badcase_registry import (
    BadCaseContractError,
    BadCaseNotFoundError,
    BadCaseRegistry,
    BadCaseSeverity,
    BadCaseStage,
    BadCaseStatus,
    BadCaseTransitionError,
    IntentFeedbackStatus,
)


IDENTITY_SALT = "test-badcase-identity-salt-at-least-32-bytes"


def registry_at(path):
    return BadCaseRegistry(str(path), identity_salt=IDENTITY_SALT)


def observe(registry: BadCaseRegistry, **overrides):
    values = {
        "source": "verifier",
        "stage": BadCaseStage.VERIFICATION,
        "severity": BadCaseSeverity.P1,
        "symptom_code": "verification_reject",
        "user_id": "real-user@example.com",
        "sanitized_input": "订单错误，token=abc123，联系 me@example.com",
        "published_response": "已转人工",
        "trace_id": "trace-12345678",
        "request_id": "request-1",
        "evidence": {"authorization": "Bearer secret", "reason_code": "coverage_gap"},
        "versions": {"commit_sha": "abc123"},
    }
    values.update(overrides)
    return registry.observe(**values)


def reproduction(message="订单错误"):
    digest = hashlib.sha256(b"owner fixture evidence").hexdigest()
    return {
        "input": {"message": message},
        "fixture": "tests.fixtures.run_verifier_owner",
        "owner": "services.answer_verifier.AnswerVerifier",
        "assertions": ["verification_status"],
        "evidence_sha256": digest,
    }


def record_prediction(registry: BadCaseRegistry, **overrides):
    values = {
        "request_id": "intent-request-1",
        "trace_id": "intent-trace-1",
        "conv_id": "intent-conv-1",
        "user_id": "signed-user",
        "input_fingerprint": "a" * 64,
        "sanitized_input": "这笔钱不是我付的，联系 me@example.com",
        "predicted_intent": "payment_issue",
        "confidence": 0.76,
        "source_scores": {"llm": 0.8, "embedding": 0.65, "pattern": 0.5},
        "classifier_fingerprint": "b" * 64,
        "bundle_version": "agent-v17",
    }
    values.update(overrides)
    return registry.record_intent_prediction(**values)


def move_to_regression_pass(registry: BadCaseRegistry, badcase_id: str):
    registry.transition(badcase_id, BadCaseStatus.TRIAGED, actor="reviewer")
    registry.transition(
        badcase_id,
        BadCaseStatus.REPRODUCED,
        actor="reviewer",
        root_cause="发布校验错误拒绝有证据的订单回答",
        owner_module="services.answer_verifier.AnswerVerifier",
        eval_layer="stateful",
        expected_behavior={"assertions": {"verification_status": "pass"}},
        reproduction=reproduction(),
    )
    registry.transition(badcase_id, BadCaseStatus.FIXING, actor="owner")
    return registry.transition(
        badcase_id,
        BadCaseStatus.REGRESSION_PASS,
        actor="owner",
        fixed_by_commit="a" * 40,
    )


def test_observation_is_redacted_hashed_and_deduplicated(tmp_path):
    registry = registry_at(tmp_path / "badcases.db")
    first, created = observe(registry)
    second, created_again = observe(
        registry, trace_id="trace-new", severity=BadCaseSeverity.P0,
    )

    assert created is True
    assert created_again is False
    assert first.badcase_id == second.badcase_id
    assert second.occurrence_count == 2
    assert second.severity is BadCaseSeverity.P0
    assert "abc123" not in second.sanitized_input
    assert "me@example.com" not in second.sanitized_input
    assert second.evidence["authorization"] == "[REDACTED]"
    assert second.user_ref != "real-user@example.com"
    assert registry.stats()["recurrences"] == 1
    assert len(registry.get_view(first.badcase_id)["occurrences"]) == 2


def test_observation_without_server_identity_salt_fails_closed(tmp_path):
    registry = BadCaseRegistry(str(tmp_path / "badcases.db"))
    with pytest.raises(BadCaseContractError, match="identity_salt"):
        observe(registry)


def test_state_machine_requires_root_cause_expected_and_real_reproduction_evidence(tmp_path):
    registry = registry_at(tmp_path / "badcases.db")
    case, _ = observe(registry)

    with pytest.raises(BadCaseTransitionError):
        registry.transition(case.badcase_id, BadCaseStatus.FIXING, actor="reviewer")

    registry.transition(case.badcase_id, BadCaseStatus.TRIAGED, actor="reviewer")
    with pytest.raises(BadCaseContractError, match="reproduction evidence missing"):
        registry.transition(
            case.badcase_id,
            BadCaseStatus.REPRODUCED,
            actor="reviewer",
            root_cause="wrong verifier contract",
            owner_module="services.answer_verifier",
            eval_layer="stateful",
            expected_behavior={"assertions": {"verification_status": "pass"}},
            reproduction={"input": {"message": "订单错误"}},
        )


def test_closed_case_recurrence_is_reopened_and_audited(tmp_path):
    registry = registry_at(tmp_path / "badcases.db")
    case, _ = observe(registry)
    move_to_regression_pass(registry, case.badcase_id)
    registry.link_regression(case.badcase_id, "bc-regression-1", actor="exporter")
    registry.transition(case.badcase_id, BadCaseStatus.VERIFIED, actor="reviewer")
    registry.transition(case.badcase_id, BadCaseStatus.CLOSED, actor="reviewer")

    reopened, created = observe(registry, trace_id="trace-recurrence")
    view = registry.get_view(case.badcase_id)

    assert created is False
    assert reopened.status is BadCaseStatus.TRIAGED
    assert reopened.occurrence_count == 2
    assert view["events"][-1]["note"] == "recurrence observed from verifier"
    assert len(view["occurrences"]) == 2


def test_export_is_dev_provisional_and_links_only_after_valid_bundle(tmp_path):
    registry = registry_at(tmp_path / "badcases.db")
    case, _ = observe(registry)
    move_to_regression_pass(registry, case.badcase_id)

    with pytest.raises(BadCaseContractError, match="linked regression case"):
        registry.transition(case.badcase_id, BadCaseStatus.VERIFIED, actor="reviewer")

    bundle = export_badcases(
        registry,
        [case.badcase_id],
        tmp_path / "badcase-regression-v1",
        actor="reviewer@example.com",
    )
    exported = bundle.cases[0]

    assert exported.split == "dev"
    assert exported.review["status"] == "provisional"
    assert "consumed_regression" in exported.tags
    assert registry.get(case.badcase_id).linked_case_id == exported.case_id


def test_user_feedback_identity_is_server_owned_and_remains_candidate(tmp_path, monkeypatch):
    registry = registry_at(tmp_path / "badcases.db")
    monkeypatch.setattr(main, "_badcase_registry", registry)
    principal = Principal(subject="signed-user", scopes=frozenset({"chat"}))
    body = main.BadCaseFeedbackRequest(
        request_id="request-2",
        category="security_false_positive",
        message="正常问题被拦截",
        correction="应该允许",
    )

    response = asyncio.run(main.submit_badcase_feedback(body, principal))
    case = registry.get(response["badcase_id"])

    assert response["created"] is True
    assert case.status is BadCaseStatus.CANDIDATE
    assert case.stage is BadCaseStage.INPUT_SECURITY
    assert case.user_ref == hmac.new(
        IDENTITY_SALT.encode(), b"signed-user", hashlib.sha256
    ).hexdigest()[:20]


def test_intent_prediction_feedback_and_annotation_are_separate_versioned_facts(tmp_path):
    registry = registry_at(tmp_path / "intent-learning.db")
    prediction = record_prediction(registry)

    assert prediction.feedback_status is IntentFeedbackStatus.PREDICTED
    assert "me@example.com" not in prediction.sanitized_input
    assert prediction.classifier_fingerprint == "b" * 64

    learning, case, created = registry.submit_intent_feedback(
        prediction_id=prediction.prediction_id,
        user_id="signed-user",
        suggested_intent="account_security",
        reason="用户明确说明并非本人交易",
    )

    assert created is True
    assert learning.feedback_status is IntentFeedbackStatus.PENDING
    assert case.stage is BadCaseStage.INTENT
    assert case.prediction_id == prediction.prediction_id
    assert case.predicted_intent == "payment_issue"
    assert case.suggested_intent == "account_security"
    assert case.approved_intent == ""

    approved, annotated_case = registry.review_intent_feedback(
        prediction.prediction_id,
        decision=IntentFeedbackStatus.APPROVED,
        actor="reviewer-17",
        approved_intent="account_security",
        dataset_version="intent-dataset-v8",
        note="工单与上下文确认",
    )

    assert approved.feedback_status is IntentFeedbackStatus.APPROVED
    assert approved.approved_intent == "account_security"
    assert approved.annotation_id
    assert annotated_case.status is BadCaseStatus.TRIAGED
    assert annotated_case.expected_behavior["intent"] == "account_security"
    assert annotated_case.annotation_id == approved.annotation_id
    view = registry.get_view(case.badcase_id)
    assert [event["event_type"] for event in view["intent_learning_events"]] == [
        "prediction_recorded", "feedback_submitted", "feedback_approved",
    ]


def test_intent_feedback_enforces_prediction_owner_and_immutable_review(tmp_path):
    registry = registry_at(tmp_path / "intent-learning.db")
    prediction = record_prediction(registry)

    with pytest.raises(BadCaseNotFoundError, match="prediction not found"):
        registry.submit_intent_feedback(
            prediction_id=prediction.prediction_id,
            user_id="another-user",
            suggested_intent="account_security",
        )

    registry.submit_intent_feedback(
        prediction_id=prediction.prediction_id,
        user_id="signed-user",
        suggested_intent="account_security",
    )
    registry.review_intent_feedback(
        prediction.prediction_id,
        decision=IntentFeedbackStatus.APPROVED,
        actor="reviewer",
        approved_intent="account_security",
        dataset_version="intent-v1",
    )
    with pytest.raises(BadCaseContractError, match="immutable"):
        registry.review_intent_feedback(
            prediction.prediction_id,
            decision=IntentFeedbackStatus.APPROVED,
            actor="reviewer-2",
            approved_intent="refund",
            dataset_version="intent-v2",
        )

    rejected_prediction = record_prediction(
        registry,
        request_id="intent-request-2",
        input_fingerprint="c" * 64,
    )
    registry.submit_intent_feedback(
        prediction_id=rejected_prediction.prediction_id,
        user_id="signed-user",
        suggested_intent="account_security",
    )
    with pytest.raises(BadCaseContractError, match="must differ"):
        registry.review_intent_feedback(
            rejected_prediction.prediction_id,
            decision=IntentFeedbackStatus.APPROVED,
            actor="reviewer",
            approved_intent="payment_issue",
            dataset_version="intent-v1",
        )
    rejected, rejected_case = registry.review_intent_feedback(
        rejected_prediction.prediction_id,
        decision=IntentFeedbackStatus.REJECTED,
        actor="reviewer",
        note="原分类正确，回答内容需要另行复核",
    )
    assert rejected.feedback_status is IntentFeedbackStatus.REJECTED
    assert rejected.annotation_id == ""
    assert rejected_case.annotation_id == ""
    assert rejected_case.status is BadCaseStatus.CANDIDATE


def test_wrong_route_api_requires_real_prediction_and_admin_review_does_not_publish(tmp_path, monkeypatch):
    registry = registry_at(tmp_path / "intent-api.db")
    prediction = record_prediction(registry)
    monkeypatch.setattr(main, "_badcase_registry", registry)

    feedback = asyncio.run(main.submit_badcase_feedback(
        main.BadCaseFeedbackRequest(
            request_id="intent-request-1",
            category="wrong_route",
            prediction_id=prediction.prediction_id,
            suggested_intent="account_security",
            correction="不是本人交易",
        ),
        Principal(subject="signed-user", scopes=frozenset({"chat"})),
    ))
    reviewed = asyncio.run(main.review_intent_feedback(
        prediction.prediction_id,
        main.IntentFeedbackReviewRequest(
            decision="approved",
            approved_intent="account_security",
            dataset_version="intent-dataset-v1",
        ),
        Principal(subject="signed-admin", scopes=frozenset({"admin"})),
    ))

    assert feedback["feedback_status"] == "pending"
    assert reviewed["intent_learning"]["feedback_status"] == "approved"
    assert reviewed["active_bundle_changed"] is False


def test_admin_api_uses_signed_actor_and_exposes_audited_queue(tmp_path, monkeypatch):
    registry = registry_at(tmp_path / "badcases.db")
    case, _ = observe(registry)
    monkeypatch.setattr(main, "_badcase_registry", registry)
    principal = Principal(subject="signed-admin", scopes=frozenset({"admin"}))

    transitioned = asyncio.run(main.transition_badcase(
        case.badcase_id,
        main.BadCaseTransitionRequest(status=BadCaseStatus.TRIAGED, note="confirmed"),
        principal,
    ))
    queue = asyncio.run(main.list_badcases(
        status=BadCaseStatus.TRIAGED,
        stage=None,
        severity=None,
        limit=50,
        _principal=principal,
    ))
    view = asyncio.run(main.get_badcase(case.badcase_id, principal))

    assert transitioned["status"] == "triaged"
    assert queue["count"] == 1
    assert view["events"][-1]["actor"] == "signed-admin"


def test_chat_failures_capture_verifier_coverage_and_unknown_tool_effect(tmp_path, monkeypatch):
    registry = registry_at(tmp_path / "badcases.db")
    monkeypatch.setattr(main, "_badcase_registry", registry)
    monkeypatch.setattr(main, "_model_policy", None)
    monkeypatch.setattr(main, "current_trace_id", lambda: "trace-capture-1")
    result = SimpleNamespace(
        intent=SimpleNamespace(value="refund"),
        coverage={"complete": False, "unresolved_required_task_ids": ["billing"]},
        task_plan={"required_task_ids": ["billing"]},
    )
    verification = VerificationResult(
        status=VerificationStatus.REJECT,
        grounded=False,
        need_escalation=True,
        reason="coverage incomplete",
        reason_code=VerificationReasonCode.INCOMPLETE,
    )

    asyncio.run(main._capture_chat_badcases(
        req=main.ChatRequest(message="帮我退款"),
        user_id="signed-user",
        request_id="request-3",
        result=result,
        verification=verification,
        published_response="已转交人工",
        tool_audit=[{
            "tool_name": "refund_request_create",
            "status": "timeout",
            "effect_status": "outcome_unknown",
            "risk": "high",
            "read_only": False,
            "approved": True,
            "params_hash": "hash-only",
        }],
    ))

    cases = registry.list(limit=20)
    assert {(case.stage.value, case.severity.value) for case in cases} == {
        ("verification", "p1"),
        ("coverage", "p1"),
        ("tool_policy", "p0"),
    }
    assert all(case.published_response == "已转交人工" for case in cases)
