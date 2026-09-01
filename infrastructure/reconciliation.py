"""Canonical count/checksum evidence for owner-to-owner data migrations."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class ReconciliationStatus(str, Enum):
    MATCHED = "matched"
    COUNT_MISMATCH = "count_mismatch"
    CONTENT_MISMATCH = "content_mismatch"


@dataclass(frozen=True)
class DataSnapshot:
    snapshot_id: str
    object_name: str
    high_watermark: str
    record_count: int
    sha256: str


@dataclass(frozen=True)
class ReconciliationResult:
    status: ReconciliationStatus
    source: DataSnapshot
    target: DataSnapshot

    @property
    def matched(self) -> bool:
        return self.status is ReconciliationStatus.MATCHED


def snapshot_records(
    *,
    snapshot_id: str,
    object_name: str,
    high_watermark: str,
    records: Iterable[Mapping[str, Any]],
) -> DataSnapshot:
    if not str(snapshot_id or "").strip() or not str(object_name or "").strip():
        raise ValueError("snapshot_id and object_name are required")
    digest = hashlib.sha256()
    count = 0
    for record in records:
        encoded = json.dumps(
            dict(record), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return DataSnapshot(
        snapshot_id=snapshot_id,
        object_name=object_name,
        high_watermark=str(high_watermark),
        record_count=count,
        sha256=digest.hexdigest(),
    )


def reconcile(source: DataSnapshot, target: DataSnapshot) -> ReconciliationResult:
    if source.object_name != target.object_name:
        raise ValueError("cannot reconcile different object names")
    if source.record_count != target.record_count:
        status = ReconciliationStatus.COUNT_MISMATCH
    elif source.sha256 != target.sha256:
        status = ReconciliationStatus.CONTENT_MISMATCH
    else:
        status = ReconciliationStatus.MATCHED
    return ReconciliationResult(status=status, source=source, target=target)
