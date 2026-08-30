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
    BadCaseRegistry,
    BadCaseSeverity,
    BadCaseStage,
    BadCaseStatus,
    BadCaseTransitionError,
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
