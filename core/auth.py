"""HTTP 身份边界：验证 JWT 并产生服务端 Principal。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet, Iterable

import jwt


class AuthenticationError(ValueError):
    """认证失败的领域错误，避免向客户端泄漏解码细节。"""


class AuthorizationError(ValueError):
    """已认证 Principal 缺少必要 scope。"""


@dataclass(frozen=True)
class Principal:
    """通过签名验证的服务端身份；请求体不能构造该对象。"""

    subject: str
    scopes: FrozenSet[str]

    def has_scope(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes

    def require(self, scopes: Iterable[str]) -> None:
        missing = [scope for scope in scopes if not self.has_scope(scope)]
        if missing:
            raise AuthorizationError("missing required scope")


class JWTAuthenticator:
    """HS256 JWT 验证器；固定 issuer/audience 并要求 exp/sub。"""

    def __init__(self, secret: str, *, issuer: str, audience: str):
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("AUTH_JWT_SECRET must contain at least 32 bytes")
        self._secret = secret
        self._issuer = issuer
        self._audience = audience

    @classmethod
    def from_env(cls) -> "JWTAuthenticator":
        secret = os.getenv("AUTH_JWT_SECRET", "")
        if not secret:
            raise RuntimeError("AUTH_JWT_SECRET is required")
        return cls(
            secret,
            issuer=os.getenv("AUTH_JWT_ISSUER", "dialogpilot"),
            audience=os.getenv("AUTH_JWT_AUDIENCE", "dialogpilot-api"),
        )

    def authenticate(self, authorization: str) -> Principal:
        scheme, separator, token = str(authorization or "").partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("bearer token required")
        try:
            claims = jwt.decode(
                token.strip(),
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["sub", "exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid bearer token") from exc
        subject = str(claims.get("sub") or "").strip()
        if not subject or len(subject) > 200:
            raise AuthenticationError("invalid bearer token")
        raw_scope = claims.get("scope", "")
        if isinstance(raw_scope, str):
            scopes = frozenset(part for part in raw_scope.split() if part)
        elif isinstance(raw_scope, list):
            scopes = frozenset(str(part) for part in raw_scope if str(part).strip())
        else:
            scopes = frozenset()
        return Principal(subject=subject, scopes=scopes)
