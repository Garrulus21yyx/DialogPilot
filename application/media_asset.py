"""Attachment identity, admission security and lifecycle contracts."""
from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import Protocol


class MediaAssetError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class UnsupportedModality(MediaAssetError):
    def __init__(self, modality: str):
        super().__init__("UNSUPPORTED_MODALITY", f"unsupported modality: {modality}")


class AssetModality(str, Enum):
    IMAGE = "IMAGE"
    DOCUMENT = "DOCUMENT"


class AssetStatus(str, Enum):
    UPLOADED = "UPLOADED"
    SCANNING = "SCANNING"
    SCANNED = "SCANNED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class ScanOutcome(str, Enum):
    CLEAN = "CLEAN"
    MALICIOUS = "MALICIOUS"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AssetAdmission:
    asset_id: str
    tenant_id: str
    user_id: str
    turn_key: str
    filename: str
    modality: AssetModality
    media_type: str
    byte_size: int
    checksum: str
    status: AssetStatus = AssetStatus.UPLOADED
    schema_version: str = "media-asset-v1"

    def __post_init__(self) -> None:
        required = (
            self.asset_id, self.tenant_id, self.user_id, self.turn_key,
            self.filename, self.media_type, self.checksum,
        )
        if any(not value for value in required) or self.byte_size < 1:
            raise MediaAssetError("INVALID_ASSET", "asset identity is incomplete")
        if len(self.checksum) != 64:
            raise MediaAssetError("INVALID_ASSET", "asset checksum must be SHA-256")


@dataclass(frozen=True)
class ImagePart:
    asset_id: str
    region_key: str | None = None
    kind: str = "image"


@dataclass(frozen=True)
class DocumentPart:
    asset_id: str
    page_hint: int | None = None
    kind: str = "document"


@dataclass(frozen=True)
class TextPart:
    text: str
    kind: str = "text"


class MalwareScannerPort(Protocol):
    def scan(self, content: bytes, *, media_type: str) -> ScanOutcome: ...


class AssetAdmissionPolicy:
    """Pure pre-storage policy. It never invokes OCR, VLM or an Agent."""

    version = "media-admission-policy-v1"
    supported = {
        "image/png": (AssetModality.IMAGE, frozenset({".png"})),
        "image/jpeg": (AssetModality.IMAGE, frozenset({".jpg", ".jpeg"})),
        "application/pdf": (AssetModality.DOCUMENT, frozenset({".pdf"})),
    }

    def __init__(self, *, max_bytes: int = 10 * 1024 * 1024):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes

    def admit(
        self,
        *,
        tenant_id: str,
        user_id: str,
        turn_key: str,
        filename: str,
        declared_media_type: str,
        content: bytes,
    ) -> AssetAdmission:
        identity = tuple(
            str(value or "").strip()
            for value in (tenant_id, user_id, turn_key, filename)
        )
        if any(not value for value in identity):
            raise MediaAssetError("INVALID_ASSET", "asset binding is incomplete")
        if not content:
            raise MediaAssetError("EMPTY_ASSET", "attachment is empty")
        if len(content) > self.max_bytes:
            raise MediaAssetError("ASSET_TOO_LARGE", "attachment exceeds byte limit")
        sniffed = _sniff(content)
        declared = str(declared_media_type or "").split(";", 1)[0].strip().lower()
        if declared.startswith(("audio/", "video/")):
            raise UnsupportedModality(declared.split("/", 1)[0])
        if sniffed not in self.supported:
            raise MediaAssetError("UNSUPPORTED_MEDIA_TYPE", "attachment type is unsupported")
        if declared != sniffed:
            raise MediaAssetError("MEDIA_TYPE_MISMATCH", "declared and sniffed MIME differ")
        extension = PurePath(filename).suffix.lower()
        modality, extensions = self.supported[sniffed]
        if extension not in extensions:
            raise MediaAssetError("FILE_EXTENSION_MISMATCH", "filename extension is invalid")
        guessed = mimetypes.guess_type(filename)[0]
        if guessed and guessed != sniffed:
            raise MediaAssetError("FILE_EXTENSION_MISMATCH", "filename MIME is invalid")
        checksum = hashlib.sha256(content).hexdigest()
        asset_hash = hashlib.sha256(
            f"{tenant_id}\0{user_id}\0{checksum}".encode("utf-8")
        ).hexdigest()
        return AssetAdmission(
            asset_id=f"asset:v1:{asset_hash}", tenant_id=tenant_id,
            user_id=user_id, turn_key=turn_key, filename=PurePath(filename).name,
            modality=modality, media_type=sniffed, byte_size=len(content),
            checksum=checksum,
        )


class AssetLifecycle:
    """Closed upload/security state machine; SCANNED means L0-safe, not parsed."""

    transitions = {
        AssetStatus.UPLOADED: frozenset({AssetStatus.SCANNING}),
        AssetStatus.SCANNING: frozenset({
            AssetStatus.SCANNED, AssetStatus.FAILED, AssetStatus.QUARANTINED,
        }),
        AssetStatus.SCANNED: frozenset(),
        AssetStatus.FAILED: frozenset(),
        AssetStatus.QUARANTINED: frozenset(),
    }

    @classmethod
    def transition(cls, current: AssetStatus, target: AssetStatus) -> AssetStatus:
        current, target = AssetStatus(current), AssetStatus(target)
        if target not in cls.transitions[current]:
            raise MediaAssetError(
                "INVALID_ASSET_TRANSITION",
                f"illegal asset transition: {current.value}->{target.value}",
            )
        return target

    @classmethod
    def scan_target(cls, outcome: ScanOutcome) -> AssetStatus:
        return {
            ScanOutcome.CLEAN: AssetStatus.SCANNED,
            ScanOutcome.MALICIOUS: AssetStatus.QUARANTINED,
            ScanOutcome.FAILED: AssetStatus.FAILED,
        }[ScanOutcome(outcome)]


def _sniff(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    return "application/octet-stream"
