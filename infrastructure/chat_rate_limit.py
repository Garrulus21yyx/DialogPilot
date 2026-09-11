"""Authenticated request quotas using the limits Redis implementation."""
import hashlib
import json
import os

from limits import parse
from limits.storage import RedisStorage
from limits.strategies import FixedWindowRateLimiter
from core.capacity_metrics import decisions


class ChatRateExceeded(RuntimeError):
    pass


class ChatRateLimiter:
    def __init__(self, redis, *, user_limit=None, tenant_limit=None):
        self.limiter = FixedWindowRateLimiter(RedisStorage(
            "redis://", connection_pool=redis.connection_pool,
            key_prefix="dialogpilot:chat-quota:v1"))
        self.user_limit = parse(user_limit or os.getenv("CHAT_USER_RATE", "30/minute"))
        self.tenant_limit = parse(tenant_limit or os.getenv("CHAT_TENANT_RATE", "300/minute"))
        if any(limit.amount < 1 or limit.get_expiry() < 1
               for limit in (self.user_limit, self.tenant_limit)):
            raise ValueError("chat quotas must have positive amounts and expiry")

    def check(self, tenant, subject):
        # Keys contain no raw identity. These short-lived operational counters
        # are not a conversation/history store. Denied requests consume allowance.
        for limit, scope in ((self.tenant_limit, (tenant,)),
                             (self.user_limit, (tenant, subject))):
            key = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
            if not self.limiter.hit(limit, key):
                decisions.labels("chat_quota", "rejected").inc()
                raise ChatRateExceeded("CHAT_RATE_EXCEEDED")
        decisions.labels("chat_quota", "allowed").inc()
