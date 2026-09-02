"""Public ResponseDelivery value objects shared by the PostgreSQL owner and API."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class DeliveryStatus(str, Enum):
    SELECTED = "selected"
    DELIVERED = "delivered"
    READ = "read"


class ResponseDeliveryError(Exception):
    """Base error for final-response delivery operations."""


class ResponseNotFoundError(ResponseDeliveryError):
    """The response does not exist or belongs to another authenticated user."""


@dataclass(frozen=True)
class ResponseDelivery:
    response_id: str
    user_id: str
    conv_id: str
    request_id: str
    seq: int
    response_text: str
    status: DeliveryStatus
    selected_at: str
    delivered_at: str | None
    read_at: str | None
    identity_metadata: dict[str, str] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("user_id", None)
        data.pop("identity_metadata", None)
        data["status"] = self.status.value
        return data
