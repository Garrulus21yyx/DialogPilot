"""Provenance-backed entity candidates for conversation planning and execution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


# CJK prose may touch an ID; ASCII identifier characters must not be sliced.
_REFERENCE = re.compile(r"(?<![A-Za-z_\d])[A-Za-z]{1,12}[-_]?\d{2,64}(?![A-Za-z_\d])")


class EntityBindingError(ValueError):
    pass


class BindingSource(str, Enum):
    PENDING_INTERACTION = "PENDING_INTERACTION"
    STRUCTURED_INPUT = "STRUCTURED_INPUT"
    CURRENT_MESSAGE = "CURRENT_MESSAGE"
    WORKSTREAM_SLOT = "WORKSTREAM_SLOT"
    RECENT_MESSAGE = "RECENT_MESSAGE"
    SUMMARY = "SUMMARY"


class BindingStatus(str, Enum):
    UNIQUE = "UNIQUE"
    AMBIGUOUS = "AMBIGUOUS"
    MISSING = "MISSING"
    STALE = "STALE"
    UNAUTHORIZED = "UNAUTHORIZED"


@dataclass(frozen=True)
class EntityBinding:
    field_name: str
    value_json: str
    source: BindingSource
    source_ref: str
    tenant_id: str
    user_id: str
    conversation_id: str
    priority: int
    source_version: int | None = None
    workstream_id: str | None = None
    valid_until: str | None = None

    @classmethod
    def create(cls, field_name: str, value: object, **kwargs) -> "EntityBinding":
        return cls(
            field_name=field_name,
            value_json=json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            ),
            **kwargs,
        )

    def __post_init__(self) -> None:
        if any(not str(value or "").strip() for value in (
            self.field_name, self.source_ref, self.tenant_id,
            self.user_id, self.conversation_id,
        )):
            raise EntityBindingError("binding identity, source, and scope are required")
        if self.priority < 0:
            raise EntityBindingError("binding priority cannot be negative")
        try:
            value = json.loads(self.value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise EntityBindingError("binding value must be JSON") from exc
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        if canonical != self.value_json:
            raise EntityBindingError("binding value must use canonical JSON")
        if self.source is BindingSource.WORKSTREAM_SLOT and not all((
            self.workstream_id, self.source_version,
        )):
            raise EntityBindingError("workstream binding requires identity and version")
        if self.valid_until is not None:
            expiry = datetime.fromisoformat(self.valid_until)
            if expiry.utcoffset() is None:
                raise EntityBindingError("binding expiry must be timezone-aware")

    @property
    def value(self) -> object:
        return json.loads(self.value_json)

    def valid_for(self, state, *, now: datetime | None = None) -> BindingStatus:
        if (
            self.tenant_id != str(state.tenant_id)
            or self.user_id != str(state.user_id)
            or self.conversation_id != str(state.conversation_id)
        ):
            return BindingStatus.UNAUTHORIZED
        if self.valid_until is not None and (now or datetime.now(timezone.utc)) >= (
            datetime.fromisoformat(self.valid_until)
        ):
            return BindingStatus.STALE
        if self.source is BindingSource.WORKSTREAM_SLOT:
            stream = next((
                item for item in state.workstreams
                if item.workstream_id == self.workstream_id
            ), None)
            if stream is None or stream.state_version != self.source_version:
                return BindingStatus.STALE
        return BindingStatus.UNIQUE


@dataclass(frozen=True)
class BindingResolution:
    field_name: str
    status: BindingStatus
    candidates: tuple[EntityBinding, ...] = ()

    @property
    def selected(self) -> EntityBinding | None:
        return self.candidates[0] if self.status is BindingStatus.UNIQUE else None


@dataclass(frozen=True)
class EntityBindingSet:
    bindings: tuple[EntityBinding, ...] = ()

    def __post_init__(self) -> None:
        identities = tuple((
            item.field_name, item.value_json, item.source_ref,
        ) for item in self.bindings)
        if len(identities) != len(set(identities)):
            raise EntityBindingError("entity bindings must be unique")

    def resolve(self, field_name: str, state) -> BindingResolution:
        matching = tuple(item for item in self.bindings if item.field_name == field_name)
        if not matching:
            return BindingResolution(field_name, BindingStatus.MISSING)
        valid = tuple(
            item for item in matching
            if item.valid_for(state) is BindingStatus.UNIQUE
        )
        if not valid:
            statuses = {item.valid_for(state) for item in matching}
            status = (
                BindingStatus.UNAUTHORIZED
                if BindingStatus.UNAUTHORIZED in statuses else BindingStatus.STALE
            )
            return BindingResolution(field_name, status, matching)
        priority = max(item.priority for item in valid)
        leading = tuple(item for item in valid if item.priority == priority)
        values = {item.value_json for item in leading}
        if len(values) != 1:
            return BindingResolution(field_name, BindingStatus.AMBIGUOUS, leading)
        return BindingResolution(field_name, BindingStatus.UNIQUE, (leading[0],))

    def as_payload(self, state) -> list[dict[str, object]]:
        fields = tuple(dict.fromkeys(item.field_name for item in self.bindings))
        return [
            {
                "field_name": field,
                "status": resolution.status.value,
                "candidates": [
                    {
                        "value": item.value,
                        "source_ref": item.source_ref,
                        "source": item.source.value,
                    }
                    for item in resolution.candidates
                ],
            }
            for field in fields
            for resolution in (self.resolve(field, state),)
        ]


class EntityBindingResolver:
    """Project scoped candidates without interpreting the user's goal."""

    version = "entity-binding-resolver-v1"

    def __init__(self, *, history_ttl: timedelta = timedelta(days=30)) -> None:
        self._history_ttl = history_ttl

    def resolve(self, observations, state, turn_context) -> EntityBindingSet:
        scope = {
            "tenant_id": str(state.tenant_id),
            "user_id": str(state.user_id),
            "conversation_id": str(state.conversation_id),
        }
        bindings = []
        for name, value in observations.structured_fields:
            bindings.append(EntityBinding.create(
                name, value,
                source=BindingSource.STRUCTURED_INPUT,
                source_ref=f"turn-field:{name}", priority=500, **scope,
            ))
        bindings.extend(self._references(
            observations.raw_text,
            field_name="order_id",
            source=BindingSource.CURRENT_MESSAGE,
            source_ref="turn-message:current",
            priority=400,
            scope=scope,
        ))
        for stream in state.active_workstreams:
            for slot in stream.slots:
                bindings.append(EntityBinding.create(
                    slot.name, slot.value,
                    source=BindingSource.WORKSTREAM_SLOT,
                    source_ref=f"workstream:{stream.workstream_id}:slot:{slot.name}",
                    priority=300, source_version=stream.state_version,
                    workstream_id=stream.workstream_id, **scope,
                ))
        for message in turn_context.recent_messages:
            expiry = self._expiry(message.observed_at)
            bindings.extend(self._references(
                message.content,
                field_name="order_id",
                source=BindingSource.RECENT_MESSAGE,
                source_ref=message.source_ref,
                priority=200,
                scope=scope,
                valid_until=expiry,
            ))
        if turn_context.summary is not None:
            bindings.extend(self._references(
                turn_context.summary.content,
                field_name="order_id",
                source=BindingSource.SUMMARY,
                source_ref=turn_context.summary.source_ref,
                priority=100,
                scope=scope,
            ))
        return EntityBindingSet(tuple(bindings))

    @staticmethod
    def _references(
        content, *, field_name, source, source_ref, priority, scope,
        valid_until=None,
    ):
        return tuple(EntityBinding.create(
            field_name, value, source=source,
            source_ref=f"{source_ref}:reference:{index}", priority=priority,
            valid_until=valid_until, **scope,
        ) for index, value in enumerate(_REFERENCE.findall(content), start=1))

    def _expiry(self, observed_at: str | None) -> str | None:
        if not observed_at:
            return None
        observed = datetime.fromisoformat(observed_at)
        if observed.utcoffset() is None:
            return None
        return (observed + self._history_ttl).isoformat()
