"""HTTP 身份所有权和用户可见诊断投影的安全不变式。"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from api import main
from core.auth import AuthenticationError, JWTAuthenticator, Principal


SECRET = "test-secret-that-is-at-least-32-bytes-long"


def token(*, subject="user-1", scope="chat", secret=SECRET):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": subject,
            "scope": scope,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": "dialogpilot",
            "aud": "dialogpilot-api",
        },
        secret,
        algorithm="HS256",
    )


def authenticator():
    return JWTAuthenticator(SECRET, issuer="dialogpilot", audience="dialogpilot-api")


def test_signed_subject_is_authoritative_and_body_mismatch_is_rejected():
    """证明 user_id 不能通过请求体伪造为其他用户。"""
    principal = authenticator().authenticate(f"Bearer {token()}")
    assert main._subject_for_request(None, principal) == "user-1"
    assert main._subject_for_request("user-1", principal) == "user-1"
    with pytest.raises(HTTPException) as exc:
        main._subject_for_request("user-2", principal)
    assert exc.value.status_code == 403


def test_invalid_signature_and_missing_scope_fail_closed():
    """证明伪造签名失败，已认证身份仍必须通过 scope 校验。"""
    with pytest.raises(AuthenticationError):
        authenticator().authenticate(f"Bearer {token(secret='another-secret-that-is-at-least-32-bytes')}")
    principal = Principal(subject="user-1", scopes=frozenset())
    with pytest.raises(ValueError):
        principal.require(["chat"])


def test_public_outcomes_never_include_candidate_or_internal_error():
    """证明 Verifier 拒绝候选无法绕过 response 字段从诊断投影泄漏。"""
    public = main._public_agent_outcomes([{
        "task_id": "billing_task",
        "required": True,
        "agent_type": "billing",
        "responding_agent_type": "billing",
        "status": "success",
        "content": "未通过校验的退款承诺",
        "error": "provider response with secret",
        "agent_key": "billing_0",
        "tool_call_ids": ["sensitive-call-id"],
        "latency_ms": 12.0,
    }])
    assert public == [{
        "task_id": "billing_task",
        "required": True,
        "agent_type": "billing",
        "responding_agent_type": "billing",
        "status": "success",
        "latency_ms": 12.0,
    }]
