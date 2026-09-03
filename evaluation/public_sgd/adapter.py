"""Fail-closed adapter from official SGD annotations to DialogPilot commands.

The adapter labels only transitions made explicit by the upstream annotations.
It never infers a command from free-form system text.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from evaluation.public_sgd.contracts import (
    AdaptedCommand,
    IntentSchema,
    SlotSchema,
    SgdAdapterError,
    canonical_json,
    load_json,
    sha256_file,
    write_json,
    write_jsonl,
)


ADAPTER_VERSION = "dialogpilot-sgd-command-adapter-v2"
CASE_SCHEMA_VERSION = "dialogpilot-public-command-case-v1"
DATASET_ID = "dstc8-schema-guided-dialogue-command-v1"
FLOW_VERSION = "sgd-v1"
SUPPORTED_SPLITS = ("dev", "test")
UPSTREAM_URI = (
    "https://github.com/google-research-datasets/"
    "dstc8-schema-guided-dialogue"
)
UPSTREAM_LICENSE = "CC-BY-SA-4.0"


@dataclass
class _FlowRuntime:
    call_count: int = 0
    latest_bindings: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConversionResult:
    output_dir: Path
    manifest: Mapping[str, Any]
    reports: Mapping[str, Mapping[str, Any]]


def load_intent_schemas(path: Path) -> dict[tuple[str, str], IntentSchema]:
    raw = load_json(path)
    if not isinstance(raw, list):
        raise SgdAdapterError("SGD schema root must be a list")
    schemas: dict[tuple[str, str], IntentSchema] = {}
    for service_value in raw:
        if not isinstance(service_value, dict):
            raise SgdAdapterError("SGD service schema must be an object")
        try:
            service = str(service_value["service_name"])
            intents = service_value["intents"]
            raw_slots = service_value["slots"]
        except KeyError as exc:
            raise SgdAdapterError("SGD service schema is incomplete") from exc
        if not isinstance(intents, list):
            raise SgdAdapterError("SGD intents must be a list")
        if not isinstance(raw_slots, list):
            raise SgdAdapterError("SGD slots must be a list")
        slots = {
            str(item["name"]): SlotSchema(
                name=str(item["name"]),
                description=_slot_description(
                    str(item["name"]), str(item["description"])
                ),
                possible_values=tuple(
                    str(possible) for possible in item["possible_values"]
                ),
            )
            for item in raw_slots
        }
        for value in intents:
            schema = IntentSchema.from_dict(service, value, slots)
            key = (service, schema.intent)
            if key in schemas:
                raise SgdAdapterError(f"duplicate SGD intent schema: {key}")
            schemas[key] = schema
    if not schemas:
        raise SgdAdapterError("SGD schema contains no intents")
    return schemas


def convert_sgd(
    source_root: Path,
    output_dir: Path,
    *,
    source_commit: str,
    splits: Sequence[str] = SUPPORTED_SPLITS,
    max_dialogues: int | None = None,
) -> ConversionResult:
    """Freeze adapted cases while preserving official dev/test boundaries."""
    _validate_source(source_root, source_commit, splits, max_dialogues)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SgdAdapterError("adapter output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)

    registry_schemas = load_intent_schemas(source_root / "train" / "schema.json")
    registry = _registry_document(registry_schemas, source_commit)
    write_json(output_dir / "registry.json", registry)

    reports: dict[str, Mapping[str, Any]] = {}
    split_artifacts: dict[str, Mapping[str, Any]] = {}
    for split in splits:
        split_dir = output_dir / split
        split_dir.mkdir()
        conversations, cases, exclusions, report = _convert_split(
            source_root,
            split,
            registry_schemas,
            source_commit,
            max_dialogues=max_dialogues,
        )
        conversation_count, conversations_sha = write_jsonl(
            split_dir / "conversations.jsonl", conversations
        )
        case_count, cases_sha = write_jsonl(split_dir / "cases.jsonl", cases)
        exclusion_count, exclusions_sha = write_jsonl(
            split_dir / "exclusions.jsonl", exclusions
        )
        report = {
            **report,
            "conversation_count": conversation_count,
            "case_count": case_count,
            "exclusion_count": exclusion_count,
            "checksums": {
                "conversations_sha256": conversations_sha,
                "cases_sha256": cases_sha,
                "exclusions_sha256": exclusions_sha,
            },
        }
        write_json(split_dir / "conversion-report.json", report)
        reports[split] = report
        split_artifacts[split] = {
            "conversation_count": conversation_count,
            "case_count": case_count,
            "exclusion_count": exclusion_count,
            **report["checksums"],
        }

    manifest = {
        "schema_version": "dialogpilot-public-dataset-lock-v1",
        "dataset_id": DATASET_ID,
        "adapter_version": ADAPTER_VERSION,
        "case_schema_version": CASE_SCHEMA_VERSION,
        "upstream_uri": UPSTREAM_URI,
        "upstream_commit": source_commit,
        "upstream_license": UPSTREAM_LICENSE,
        "registry_scope": "official train/schema.json",
        "registry_sha256": sha256_file(output_dir / "registry.json"),
        "official_splits_preserved": list(splits),
        "max_dialogues": max_dialogues,
        "publication_status": (
            "DEV_SUBSET" if max_dialogues is not None else "FROZEN_FULL_SPLITS"
        ),
        "mapping_rules": {
            "SGD_SERVICE_CALL_FIRST": "RESOLVED/START_FLOW",
            "SGD_SERVICE_CALL_REPEAT": "RESOLVED/CONTINUE_FLOW",
            "SGD_REQUIRED_SLOT_REQUEST": "CLARIFY",
            "SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY": "NO_SUPPORTED_FLOW",
        },
        "splits": split_artifacts,
    }
    write_json(output_dir / "manifest.json", manifest)
    return ConversionResult(output_dir, manifest, reports)


def _validate_source(
    source_root: Path,
    source_commit: str,
    splits: Sequence[str],
    max_dialogues: int | None,
) -> None:
    if len(source_commit) != 40 or any(
        char not in "0123456789abcdef" for char in source_commit
    ):
        raise SgdAdapterError("source_commit must be a lowercase git SHA")
    if not splits or len(set(splits)) != len(splits):
        raise SgdAdapterError("splits must be non-empty and unique")
    if any(split not in SUPPORTED_SPLITS for split in splits):
        raise SgdAdapterError("only official dev and test splits are supported")
    if max_dialogues is not None and max_dialogues < 1:
        raise SgdAdapterError("max_dialogues must be positive")
    for relative in ("train/schema.json", "LICENSE.txt"):
        if not (source_root / relative).is_file():
            raise SgdAdapterError(f"missing official SGD file: {relative}")
    for split in splits:
        if not (source_root / split / "schema.json").is_file():
            raise SgdAdapterError(f"missing official SGD split: {split}")
        if not tuple((source_root / split).glob("dialogues_*.json")):
            raise SgdAdapterError(f"SGD split has no dialogue shards: {split}")


def _convert_split(
    source_root: Path,
    split: str,
    registry_schemas: Mapping[tuple[str, str], IntentSchema],
    source_commit: str,
    *,
    max_dialogues: int | None,
) -> tuple[
    list[Mapping[str, Any]],
    list[Mapping[str, Any]],
    list[Mapping[str, Any]],
    Mapping[str, Any],
]:
    split_schemas = load_intent_schemas(source_root / split / "schema.json")
    conversations: list[Mapping[str, Any]] = []
    cases: list[Mapping[str, Any]] = []
    exclusions: list[Mapping[str, Any]] = []
    mapping_counts: Counter[str] = Counter()
    exclusion_counts: Counter[str] = Counter()
    dialogue_count = 0

    stop = False
    for shard in sorted((source_root / split).glob("dialogues_*.json")):
        values = load_json(shard)
        if not isinstance(values, list):
            raise SgdAdapterError(f"dialogue shard must be a list: {shard}")
        for dialogue in values:
            if max_dialogues is not None and dialogue_count >= max_dialogues:
                stop = True
                break
            if not isinstance(dialogue, dict):
                raise SgdAdapterError("dialogue must be an object")
            dialogue_count += 1
            converted, rejected = _convert_dialogue(
                dialogue,
                split,
                registry_schemas,
                split_schemas,
                source_commit,
                shard.name,
            )
            if converted:
                conversations.append(_conversation_record(dialogue, split, shard.name))
                cases.extend(converted)
                mapping_counts.update(
                    case["provenance"]["mapping_rule"] for case in converted
                )
            exclusions.extend(rejected)
            exclusion_counts.update(item["reason_code"] for item in rejected)
        if stop:
            break

    if not cases:
        raise SgdAdapterError(f"no unambiguous cases produced for split: {split}")
    return conversations, cases, exclusions, {
        "schema_version": "dialogpilot-sgd-conversion-report-v1",
        "dataset_id": DATASET_ID,
        "split": split,
        "source_dialogue_count": dialogue_count,
        "mapping_counts": dict(sorted(mapping_counts.items())),
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
    }


def _convert_dialogue(
    dialogue: Mapping[str, Any],
    split: str,
    registry_schemas: Mapping[tuple[str, str], IntentSchema],
    split_schemas: Mapping[tuple[str, str], IntentSchema],
    source_commit: str,
    shard_name: str,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    try:
        dialogue_id = str(dialogue["dialogue_id"])
        turns = dialogue["turns"]
    except KeyError as exc:
        raise SgdAdapterError("dialogue identity/turns are missing") from exc
    if not isinstance(turns, list):
        raise SgdAdapterError("dialogue turns must be a list")

    runtimes: dict[tuple[str, str], _FlowRuntime] = {}
    cases: list[Mapping[str, Any]] = []
    exclusions: list[Mapping[str, Any]] = []
    for index, turn in enumerate(turns):
        if turn.get("speaker") != "USER":
            continue
        source_ref = _source_ref(split, shard_name, dialogue_id, index)
        frames = turn.get("frames")
        if not isinstance(frames, list) or len(frames) != 1:
            exclusions.append(_exclusion(source_ref, "MULTI_OR_MISSING_USER_FRAME"))
            continue
        frame = frames[0]
        state = frame.get("state") or {}
        service = str(frame.get("service") or "")
        intent = str(state.get("active_intent") or "")
        if not service or not intent or intent == "NONE":
            exclusions.append(_exclusion(source_ref, "NO_ACTIVE_INTENT"))
            continue
        split_schema = split_schemas.get((service, intent))
        if split_schema is None:
            exclusions.append(_exclusion(source_ref, "SOURCE_SCHEMA_MISSING"))
            continue

        history = _history_reference(dialogue_id, index)
        before_state = _state_before_turn(
            dialogue_id, service, intent, runtimes.get((service, intent))
        )
        if (service, intent) not in registry_schemas:
            cases.append(_case(
                split=split,
                dialogue_id=dialogue_id,
                turn_index=index,
                message=str(turn.get("utterance") or ""),
                history=history,
                before_state=before_state,
                status="NO_SUPPORTED_FLOW",
                reason_code="SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY",
                commands=(),
                missing_required_slots=(),
                next_state=before_state,
                source_commit=source_commit,
                source_ref=source_ref,
                mapping_rule="SGD_SERVICE_OUTSIDE_TRAIN_REGISTRY",
                source_evidence={"service": service, "intent": intent},
            ))
            continue

        if index + 1 >= len(turns) or turns[index + 1].get("speaker") != "SYSTEM":
            exclusions.append(_exclusion(source_ref, "NO_FOLLOWING_SYSTEM_TURN"))
            continue
        system_turn = turns[index + 1]
        system_frames = [
            item for item in system_turn.get("frames") or []
            if item.get("service") == service
        ]
        if len(system_frames) != 1:
            exclusions.append(_exclusion(source_ref, "NO_UNIQUE_SYSTEM_FRAME"))
            continue
        system_frame = system_frames[0]
        service_call = system_frame.get("service_call")
        if isinstance(service_call, dict) and service_call.get("method") == intent:
            parameters = service_call.get("parameters")
            if not isinstance(parameters, dict):
                exclusions.append(_exclusion(source_ref, "INVALID_SERVICE_CALL"))
                continue
            missing_call_arguments = tuple(
                slot for slot in split_schema.required_slots if slot not in parameters
            )
            if missing_call_arguments:
                exclusions.append(_exclusion(
                    source_ref,
                    "SERVICE_CALL_MISSING_REQUIRED_ARGUMENTS",
                ))
                continue
            runtime = runtimes.setdefault((service, intent), _FlowRuntime())
            first = runtime.call_count == 0
            kind = "START_FLOW" if first else "CONTINUE_FLOW"
            instance_id = flow_instance_id(dialogue_id, service, intent)
            command = AdaptedCommand(
                kind=kind,
                flow_id=split_schema.flow_id,
                flow_version=FLOW_VERSION,
                flow_instance_id=None if first else instance_id,
                arguments=dict(parameters),
            )
            runtime.call_count += 1
            runtime.latest_bindings = dict(parameters)
            next_state = _active_state(dialogue_id, split_schema, runtime)
            mapping_rule = (
                "SGD_SERVICE_CALL_FIRST" if first else "SGD_SERVICE_CALL_REPEAT"
            )
            cases.append(_case(
                split=split,
                dialogue_id=dialogue_id,
                turn_index=index,
                message=str(turn.get("utterance") or ""),
                history=history,
                before_state=before_state,
                status="RESOLVED",
                reason_code="COMMANDS_RESOLVED",
                commands=(command,),
                missing_required_slots=(),
                next_state=next_state,
                source_commit=source_commit,
                source_ref=source_ref,
                mapping_rule=mapping_rule,
                source_evidence={
                    "service": service,
                    "intent": intent,
                    "service_call": service_call,
                },
            ))
            continue

        slot_values = state.get("slot_values") or {}
        if not isinstance(slot_values, dict):
            exclusions.append(_exclusion(source_ref, "INVALID_DIALOGUE_STATE"))
            continue
        missing = tuple(
            slot for slot in split_schema.required_slots if slot not in slot_values
        )
        requested = tuple(
            str(action.get("slot"))
            for action in system_frame.get("actions") or []
            if action.get("act") == "REQUEST" and action.get("slot")
        )
        requested_missing = tuple(slot for slot in requested if slot in missing)
        if missing and requested_missing:
            cases.append(_case(
                split=split,
                dialogue_id=dialogue_id,
                turn_index=index,
                message=str(turn.get("utterance") or ""),
                history=history,
                before_state=before_state,
                status="CLARIFY",
                reason_code="SGD_REQUIRED_SLOT_MISSING",
                commands=(),
                missing_required_slots=missing,
                next_state=before_state,
                source_commit=source_commit,
                source_ref=source_ref,
                mapping_rule="SGD_REQUIRED_SLOT_REQUEST",
                source_evidence={
                    "service": service,
                    "intent": intent,
                    "required_slots": list(split_schema.required_slots),
                    "observed_slots": sorted(slot_values),
                    "requested_slots": list(requested),
                },
            ))
            continue
        exclusions.append(_exclusion(source_ref, "NO_UNAMBIGUOUS_COMMAND_LABEL"))
    return cases, exclusions


def _case(
    *,
    split: str,
    dialogue_id: str,
    turn_index: int,
    message: str,
    history: Mapping[str, Any],
    before_state: Mapping[str, Any],
    status: str,
    reason_code: str,
    commands: Sequence[AdaptedCommand],
    missing_required_slots: Sequence[str],
    next_state: Mapping[str, Any],
    source_commit: str,
    source_ref: str,
    mapping_rule: str,
    source_evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not message.strip():
        raise SgdAdapterError(f"empty user message: {source_ref}")
    return {
        "schema_version": CASE_SCHEMA_VERSION,
        "case_id": f"sgd:{split}:{dialogue_id}:turn-{turn_index}",
        "dataset_id": DATASET_ID,
        "split": split,
        "input": {
            "message": message,
            "history": dict(history),
            "current_state": dict(before_state),
        },
        "expected": {
            "status": status,
            "reason_code": reason_code,
            "commands": [item.to_dict() for item in commands],
            "missing_required_slots": list(missing_required_slots),
            "next_state": dict(next_state),
        },
        "provenance": {
            "upstream_commit": source_commit,
            "source_ref": source_ref,
            "mapping_rule": mapping_rule,
            "source_evidence": dict(source_evidence),
        },
    }


def _conversation_record(
    dialogue: Mapping[str, Any], split: str, shard_name: str
) -> Mapping[str, Any]:
    return {
        "schema_version": "dialogpilot-public-conversation-v1",
        "dialogue_id": dialogue["dialogue_id"],
        "split": split,
        "source_shard": shard_name,
        "services": dialogue.get("services") or [],
        "turns": [
            {
                "turn_index": index,
                "speaker": turn.get("speaker"),
                "utterance": turn.get("utterance"),
            }
            for index, turn in enumerate(dialogue["turns"])
        ],
    }


def _history_reference(dialogue_id: str, turn_index: int) -> Mapping[str, Any]:
    return {
        "dialogue_id": dialogue_id,
        "inclusive_start_turn": 0,
        "exclusive_end_turn": turn_index,
    }


def _state_before_turn(
    dialogue_id: str,
    service: str,
    intent: str,
    runtime: _FlowRuntime | None,
) -> Mapping[str, Any]:
    if runtime is None or runtime.call_count == 0:
        return {"active_flows": [], "pending_required_slots": []}
    return {
        "active_flows": [{
            "flow_id": f"sgd.{service}.{intent}",
            "version": FLOW_VERSION,
            "instance_id": flow_instance_id(dialogue_id, service, intent),
            "state_version": runtime.call_count,
            "bindings": dict(runtime.latest_bindings or {}),
        }],
        "pending_required_slots": [],
    }


def _active_state(
    dialogue_id: str,
    schema: IntentSchema,
    runtime: _FlowRuntime,
) -> Mapping[str, Any]:
    return {
        "active_flows": [{
            "flow_id": schema.flow_id,
            "version": FLOW_VERSION,
            "instance_id": flow_instance_id(
                dialogue_id, schema.service, schema.intent
            ),
            "state_version": runtime.call_count,
            "bindings": dict(runtime.latest_bindings or {}),
        }],
        "pending_required_slots": [],
    }


def flow_instance_id(dialogue_id: str, service: str, intent: str) -> str:
    digest = hashlib.sha256(
        canonical_json([dialogue_id, service, intent]).encode("utf-8")
    ).hexdigest()[:24]
    return f"sgd-flow-{digest}"


def _source_ref(
    split: str, shard_name: str, dialogue_id: str, turn_index: int
) -> str:
    return f"sgd://{split}/{shard_name}/{dialogue_id}/turn/{turn_index}"


def _exclusion(source_ref: str, reason_code: str) -> Mapping[str, Any]:
    return {
        "schema_version": "dialogpilot-sgd-exclusion-v1",
        "source_ref": source_ref,
        "reason_code": reason_code,
    }


def _registry_document(
    schemas: Mapping[tuple[str, str], IntentSchema], source_commit: str
) -> Mapping[str, Any]:
    flows = []
    for schema in sorted(schemas.values(), key=lambda item: item.flow_id):
        flows.append({
            "flow_id": schema.flow_id,
            "version": FLOW_VERSION,
            "service": schema.service,
            "intent": schema.intent,
            "description": schema.description,
            "transactional": schema.transactional,
            "required_arguments": list(schema.required_slots),
            "optional_arguments": list(schema.optional_slots),
            "argument_definitions": [
                {
                    "name": item.name,
                    "description": item.description,
                    "possible_values": list(item.possible_values),
                }
                for item in schema.slot_schemas
            ],
            "allowed_commands": ["START_FLOW", "CONTINUE_FLOW"],
            "effect": (
                "write_requires_approval" if schema.transactional else "read_only"
            ),
            "approval": (
                "EXPLICIT_CONFIRMATION_REQUIRED"
                if schema.transactional else "USER_COMMAND_SUFFICIENT"
            ),
            "tool": f"sgd_service:{schema.service}:{schema.intent}",
        })
    payload = {
        "schema_version": "dialogpilot-public-flow-registry-v1",
        "registry_id": "sgd-train-schema-registry-v1",
        "generation": f"sgd-{source_commit[:12]}",
        "authority": "official train/schema.json",
        "flows": flows,
    }
    payload["fingerprint"] = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    return payload


def _slot_description(name: str, description: str) -> str:
    if name == "date" or name.endswith("_date"):
        return (
            f"{description}. Use YYYY-MM-DD; in this public benchmark, "
            "today is 2019-03-01."
        )
    if name == "time" or name.endswith("_time"):
        return f"{description}. Use 24-hour HH:MM."
    return description
