"""公共客服知识库的最小来源导入合同。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Mapping, Optional


class SourceDocumentContractError(ValueError):
    """来源文档不满足当前 public-only 导入合同。"""


@dataclass(frozen=True)
class SourceDocument:
    """位于文件/API 解析边界与 Chunker 之间的不可变规范对象。

    当前只支持轻量文本来源，不承担 PDF/HTML/OCR 等结构化解析职责。
    ``scope`` 不是调用方可选择的字段：这个 collection 的合同固定为 public。
    """

    PUBLIC_SCOPE = "public"
    SUPPORTED_SOURCE_TYPES = frozenset({"text", "markdown", "json"})

    source_id: str
    title: str
    content: str
    source_type: str
    checksum: str
    region: str = "global"
    product: str = ""
    channel: str = "global"
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        source_id = str(self.source_id).strip()
        title = str(self.title).strip()
        content = str(self.content)
        source_type = str(self.source_type).strip().lower()
        checksum = str(self.checksum).strip().lower()
        if not source_id or len(source_id) > 256 or any(ord(char) < 32 for char in source_id):
            raise SourceDocumentContractError("source_id must be 1-256 printable characters")
        if not title:
            raise SourceDocumentContractError("title must not be empty")
        if not content.strip():
            raise SourceDocumentContractError("content must not be empty")
        if source_type not in self.SUPPORTED_SOURCE_TYPES:
            supported = ", ".join(sorted(self.SUPPORTED_SOURCE_TYPES))
            raise SourceDocumentContractError(f"unsupported source_type; expected one of: {supported}")
        for key in ("region", "product", "channel"):
            value = getattr(self, key)
            if not isinstance(value, str) or len(value) > 128 or any(ord(c) < 32 for c in value):
                raise SourceDocumentContractError(f"invalid {key}")
            object.__setattr__(self, key, value.strip())
        if not self.region or not self.channel:
            raise SourceDocumentContractError("region/channel must be explicit or global")
        for key in ("effective_from", "effective_to"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
                raise SourceDocumentContractError(f"{key} requires timezone-aware datetime")
        if self.effective_to is not None and (self.effective_from is None or self.effective_to <= self.effective_from):
            raise SourceDocumentContractError("effective_to requires an earlier effective_from")
        expected = self.content_checksum(content)
        if checksum != expected:
            raise SourceDocumentContractError(
                f"checksum mismatch for source_id={source_id}; expected sha256={expected}"
            )
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "checksum", checksum)

    @property
    def scope(self) -> str:
        """当前 collection 的固定可见范围，不是文档级 ACL。"""
        return self.PUBLIC_SCOPE

    @property
    def revision_id(self) -> str:
        """Content-addressed v0 revision; never synthesized from legacy labels."""
        return f"revision-v0-{self.checksum[:32]}"

    @staticmethod
    def content_checksum(content: str) -> str:
        return hashlib.sha256(str(content).encode("utf-8")).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        title: str,
        content: str,
        source_type: str = "text",
        source_id: str = "",
        checksum: Optional[str] = None,
        region: str = "global", product: str = "", channel: str = "global",
        effective_from: datetime | None = None, effective_to: datetime | None = None,
    ) -> "SourceDocument":
        normalized_type = str(source_type or "text").strip().lower()
        aliases = {"txt": "text", "md": "markdown"}
        normalized_type = aliases.get(normalized_type, normalized_type)
        calculated = cls.content_checksum(content)
        normalized_id = str(source_id or "").strip()
        if not normalized_id:
            identity = f"{normalized_type}\0{str(title).strip()}\0{content}"
            normalized_id = f"source-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
        return cls(
            source_id=normalized_id,
            title=title,
            content=content,
            source_type=normalized_type,
            checksum=checksum or calculated, region=region, product=product, channel=channel,
            effective_from=effective_from, effective_to=effective_to,
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        default_source_type: str = "text",
    ) -> "SourceDocument":
        if not isinstance(value, Mapping):
            raise SourceDocumentContractError("source document must be an object")
        supported_fields = {
            "id", "source_id", "title", "content", "source_type", "checksum", "scope",
            "region", "product", "channel", "effective_from", "effective_to",
        }
        unsupported = sorted(str(field) for field in value if field not in supported_fields)
        if unsupported:
            raise SourceDocumentContractError(
                "unsupported source document fields: " + ", ".join(unsupported)
            )
        if "scope" not in value:
            raise SourceDocumentContractError("source scope must be explicit")
        requested_scope = str(value.get("scope") or "").strip().lower()
        if requested_scope != cls.PUBLIC_SCOPE:
            raise SourceDocumentContractError(
                "this knowledge collection supports scope=public only"
            )
        return cls.create(
            source_id=str(value.get("source_id") or value.get("id") or ""),
            title=str(value.get("title") or ""),
            content=str(value.get("content") or ""),
            source_type=str(value.get("source_type") or default_source_type),
            checksum=(str(value["checksum"]) if value.get("checksum") else None),
            region=value.get("region", "global"), product=value.get("product", ""),
            channel=value.get("channel", "global"),
            effective_from=_datetime(value.get("effective_from")),
            effective_to=_datetime(value.get("effective_to")),
        )


def _datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SourceDocumentContractError("effective time must be ISO-8601 with timezone") from exc
