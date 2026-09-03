"""Adapt the pinned LoCoMo single-hop slice without creating Memory facts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.command_primary_eval.locomo_session_contracts import (
    BenchmarkMemoryCase,
    BenchmarkSessionDocument,
    LocomoSessionSlice,
)


LOCOMO_DATASET_ID = "locomo10"
LOCOMO_SOURCE_REVISION = "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376"
LOCOMO_SOURCE_SHA256 = (
    "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
)
LOCOMO_SAMPLE_ID = "conv-26"
LOCOMO_CATEGORY = 4
LOCOMO_DOCUMENT_COUNT = 19
LOCOMO_CASE_COUNT = 70

_SESSION_KEY = re.compile(r"session_(\d+)")
_DIALOG_REF = re.compile(r"D(\d+):(\d+)")


class LocomoAdapterError(ValueError):
    """The pinned public source does not satisfy the supported slice."""


@dataclass(frozen=True)
class LocomoSourcePin:
    revision: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.revision.strip() or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("LoCoMo source pin is incomplete")


OFFICIAL_LOCOMO_SOURCE = LocomoSourcePin(
    LOCOMO_SOURCE_REVISION,
    LOCOMO_SOURCE_SHA256,
)


def load_locomo_single_hop_slice(
    path: str | Path,
    *,
    source: LocomoSourcePin = OFFICIAL_LOCOMO_SOURCE,
) -> LocomoSessionSlice:
    raw = Path(path).read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != source.sha256:
        raise LocomoAdapterError(f"LoCoMo source checksum mismatch: {actual_sha256}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocomoAdapterError("LoCoMo source is not valid JSON") from exc
    if not isinstance(payload, list):
        raise LocomoAdapterError("LoCoMo source root must be a list")
    matches = [
        item
        for item in payload
        if isinstance(item, Mapping) and item.get("sample_id") == LOCOMO_SAMPLE_ID
    ]
    if len(matches) != 1:
        raise LocomoAdapterError("pinned LoCoMo sample is missing or duplicated")

    documents = _documents(matches[0])
    cases = _cases(matches[0], documents)
    if len(documents) != LOCOMO_DOCUMENT_COUNT or len(cases) != LOCOMO_CASE_COUNT:
        raise LocomoAdapterError("pinned LoCoMo slice cardinality drift")
    return LocomoSessionSlice(
        dataset_id=LOCOMO_DATASET_ID,
        source_revision=source.revision,
        source_sha256=actual_sha256,
        sample_id=LOCOMO_SAMPLE_ID,
        category=LOCOMO_CATEGORY,
        documents=documents,
        cases=cases,
    )


def _documents(sample: Mapping[str, Any]) -> tuple[BenchmarkSessionDocument, ...]:
    conversation = sample.get("conversation")
    if not isinstance(conversation, Mapping):
        raise LocomoAdapterError("LoCoMo conversation is missing")
    indexed: list[tuple[int, Sequence[Mapping[str, Any]]]] = []
    for key, value in conversation.items():
        match = _SESSION_KEY.fullmatch(str(key))
        if match is not None:
            if not isinstance(value, list) or not all(
                isinstance(turn, Mapping) for turn in value
            ):
                raise LocomoAdapterError("LoCoMo session has invalid turns")
            indexed.append((int(match.group(1)), value))

    documents = []
    for number, turns in sorted(indexed):
        occurred_at = conversation.get(f"session_{number}_date_time")
        if not isinstance(occurred_at, str) or not occurred_at.strip():
            raise LocomoAdapterError("LoCoMo session timestamp is missing")
        lines = []
        refs = []
        for turn in turns:
            speaker = turn.get("speaker")
            text = turn.get("text")
            source_ref = turn.get("dia_id")
            if not all(
                isinstance(value, str) and value.strip()
                for value in (
                    speaker,
                    text,
                    source_ref,
                )
            ):
                raise LocomoAdapterError("LoCoMo turn is incomplete")
            expected = _dialog_ref(source_ref)
            if expected[0] != number:
                raise LocomoAdapterError("LoCoMo turn belongs to the wrong session")
            lines.append(f"{speaker}: {text}")
            refs.append(source_ref)
        documents.append(
            BenchmarkSessionDocument(
                conversation_id=LOCOMO_SAMPLE_ID,
                session_id=f"S{number}",
                occurred_at=occurred_at,
                content=f"{occurred_at}\n" + "\n".join(lines),
                source_refs=tuple(refs),
            )
        )
    return tuple(documents)


def _cases(
    sample: Mapping[str, Any],
    documents: tuple[BenchmarkSessionDocument, ...],
) -> tuple[BenchmarkMemoryCase, ...]:
    raw_cases = sample.get("qa")
    if not isinstance(raw_cases, list):
        raise LocomoAdapterError("LoCoMo QA annotations are missing")
    source_refs = {
        source_ref: document.session_id
        for document in documents
        for source_ref in document.source_refs
    }
    selected = [
        item
        for item in raw_cases
        if isinstance(item, Mapping) and item.get("category") == LOCOMO_CATEGORY
    ]
    cases = []
    for ordinal, item in enumerate(selected, start=1):
        question = item.get("question")
        evidence = item.get("evidence")
        if not isinstance(question, str) or not question.strip():
            raise LocomoAdapterError("LoCoMo question is incomplete")
        if not isinstance(evidence, list) or not evidence:
            raise LocomoAdapterError("LoCoMo single-hop gold is missing")
        gold_sessions = []
        for raw_ref in evidence:
            if not isinstance(raw_ref, str):
                raise LocomoAdapterError("LoCoMo evidence ref is invalid")
            _dialog_ref(raw_ref)
            try:
                session_id = source_refs[raw_ref]
            except KeyError as exc:
                raise LocomoAdapterError(
                    f"LoCoMo evidence ref is unresolved: {raw_ref}"
                ) from exc
            if session_id not in gold_sessions:
                gold_sessions.append(session_id)
        cases.append(
            BenchmarkMemoryCase(
                question_id=(
                    f"{LOCOMO_SAMPLE_ID}:category-{LOCOMO_CATEGORY}:q{ordinal:03d}"
                ),
                conversation_id=LOCOMO_SAMPLE_ID,
                category=LOCOMO_CATEGORY,
                question=question,
                gold_session_ids=tuple(gold_sessions),
            )
        )
    return tuple(cases)


def _dialog_ref(value: str) -> tuple[int, int]:
    match = _DIALOG_REF.fullmatch(value)
    if match is None:
        raise LocomoAdapterError(f"unsupported LoCoMo evidence ref: {value}")
    return int(match.group(1)), int(match.group(2))
