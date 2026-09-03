"""Typed JSON contracts for the public SGD command benchmark."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


class SgdAdapterError(ValueError):
    """The upstream corpus or an adapted artifact violates its contract."""


@dataclass(frozen=True)
class SlotSchema:
    name: str
    description: str
    possible_values: tuple[str, ...]


@dataclass(frozen=True)
class IntentSchema:
    service: str
    intent: str
    description: str
    transactional: bool
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]
    slot_schemas: tuple[SlotSchema, ...]

    @property
    def flow_id(self) -> str:
        return f"sgd.{self.service}.{self.intent}"

    @classmethod
    def from_dict(
        cls,
        service: str,
        value: Mapping[str, Any],
        slots: Mapping[str, SlotSchema],
    ) -> "IntentSchema":
        try:
            required = tuple(str(item) for item in value["required_slots"])
            optional = tuple(sorted(str(item) for item in value["optional_slots"]))
            return cls(
                service=service,
                intent=str(value["name"]),
                description=str(value["description"]),
                transactional=bool(value["is_transactional"]),
                required_slots=required,
                optional_slots=optional,
                slot_schemas=tuple(slots[name] for name in (*required, *optional)),
            )
        except (KeyError, TypeError) as exc:
            raise SgdAdapterError("invalid SGD intent schema") from exc


@dataclass(frozen=True)
class AdaptedCommand:
    kind: str
    flow_id: str
    flow_version: str
    flow_instance_id: str | None
    arguments: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "flow": {
                "flow_id": self.flow_id,
                "version": self.flow_version,
                "instance_id": self.flow_instance_id,
            },
            "arguments": dict(self.arguments),
        }


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> tuple[int, str]:
    count = 0
    digest = hashlib.sha256()
    with path.open("wb") as handle:
        for row in rows:
            encoded = (canonical_json(row) + "\n").encode("utf-8")
            handle.write(encoded)
            digest.update(encoded)
            count += 1
    return count, digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SgdAdapterError(f"cannot read JSON: {path}") from exc


def load_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SgdAdapterError(f"cannot read JSONL: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SgdAdapterError(
                f"invalid JSONL row {line_number}: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise SgdAdapterError(f"JSONL row {line_number} must be an object")
        rows.append(value)
    return tuple(rows)
