"""Authenticated, bounded access to existing private conversation originals."""
import asyncio
import json
import re
from dataclasses import fields, replace
from types import SimpleNamespace

from jsonpointer import JsonPointerException, resolve_pointer

from application.authority_policy import AuthoritySupport, FactRequirement, RequirementEffect
from application.capability_registry import CapabilityEffect, CapabilityRisk, ToolDefinition
from infrastructure.postgres_conversation_evidence import BusinessObservationUnavailable
from mcp.tool_manager import Tool, ToolObservationEnvelope, ToolRejected

TOOL_ID = "read_conversation_observation"
AUTHORITY = "memory.business_observation"


def observation_document(original):
    document = original.model_dump(mode="json")
    for fact in document["facts"]:
        fact["value"] = json.loads(fact.pop("value_json"))
    return document


def selected_source_call_ids(original, pointer):
    """Return only fact sources actually covered by the selected JSON Pointer."""
    match = re.match(r"^/facts/(\d+)(?:/|$)", pointer)
    facts = original.facts if pointer in {"", "/facts"} else (
        (original.facts[int(match.group(1))],)
        if match and int(match.group(1)) < len(original.facts) else ()
    )
    return tuple(dict.fromkeys(
        fact.source_ref for fact in facts if isinstance(fact.source_ref, str) and fact.source_ref
    ))


def build_observation_tool(reader, principals):
    async def handler(params, context):
        context = context or {}
        scope = {key: context.get(key) for key in ("tenant_id", "user_id", "conversation_id")}
        if not all(isinstance(value, str) and value.strip() for value in scope.values()):
            raise ToolRejected("Historical observation requires authenticated conversation identity")
        try:
            original = await asyncio.to_thread(reader.read_business, SimpleNamespace(**scope),
                publication_id=params["publication_id"], observation_id=params["observation_id"])
            selected = resolve_pointer(observation_document(original), params.get("pointer", ""))
        except BusinessObservationUnavailable as exc:
            raise ToolRejected(str(exc)) from exc
        except JsonPointerException as exc:
            raise ToolRejected("JSON Pointer does not identify a value in this observation") from exc
        content = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        # JSON Schema integer includes 1.0; normalize its Python representation
        # after schema validation before using it as a sequence index.
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 2000))
        if offset > len(content):
            raise ToolRejected("Offset exceeds the selected historical value")
        end = min(offset + limit, len(content))
        payload = {"status": "HISTORICAL", "publication_id": params["publication_id"],
            "observation_id": original.observation_id, "pointer": params.get("pointer", ""),
            "encoding": "json", "offset": offset, "text": content[offset:end],
            "total_characters": len(content), "next_offset": end if end < len(content) else None,
            "complete": offset == 0 and end == len(content)}
        return ToolObservationEnvelope(payload, selected_source_call_ids(
            original, params.get("pointer", "")
        ))

    return Tool(name=TOOL_ID, description=(
        "Read an original observation from this conversation without rerunning business tools. "
        "Use publication_id and observation_id from historical context. JSON Pointer selects a value, "
        "for example /facts/0/value; empty pointer reads the observation. Output is a bounded JSON text "
        "page; follow next_offset or select a narrower pointer. Original observation times still apply: "
        "archiving does not invalidate a result or grant approval/permission to retry a write."), handler=handler,
        schema={"type": "object", "additionalProperties": False, "properties": {
            "publication_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "observation_id": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "pointer": {"type": "string", "maxLength": 4096},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
        }, "required": ["publication_id", "observation_id"]},
        allowed_agents=tuple(dict.fromkeys(principals)), read_only=True, authority=AUTHORITY,
        manifest_version="conversation-observation-v1", output_schema_version="conversation-observation-page-v1",
        preconditions=("authenticated_tenant", "authenticated_user", "authenticated_conversation"),
        idempotency="read_only", retry_policy="safe_read_retry", cache_ttl=0,
        typed_outcomes=("HISTORICAL",), output_fields=("status", "text", "complete", "observation_id"))


def install_observation_capability(registry, tool_manager, reader):
    """One native read path for default and externally supplied business bundles."""
    if not registry.agents:
        return registry
    profile = registry.verification_profiles[0].ref
    definition = ToolDefinition(TOOL_ID, "v1", "conversation-observation-input-v1",
        "conversation-observation-page-v1", CapabilityEffect.READ, CapabilityRisk.LOW,
        AUTHORITY, profile, inject_identity_fields=("tenant_id", "user_id", "conversation_id"))
    requirement = FactRequirement(AUTHORITY, AUTHORITY, ("status", "text", "observation_id"), None,
        RequirementEffect.READ, (TOOL_ID,), (), "", AuthoritySupport.SUPPORTED, "conversation-publication-v1")
    if TOOL_ID in {tool.tool_id for tool in registry.tools}:
        if (registry.tool(TOOL_ID) != definition or requirement not in registry.requirements
                or any(TOOL_ID not in agent.allowed_tool_ids for agent in registry.agents)):
            raise ValueError("conversation observation capability conflicts with runtime contract")
        updated = registry
    else:
        updated = replace(registry, tools=(*registry.tools, definition), requirements=(*registry.requirements, requirement),
            agents=tuple(replace(agent, allowed_tool_ids=(*agent.allowed_tool_ids, TOOL_ID)) for agent in registry.agents))
    tool = build_observation_tool(reader, (agent.execution_principal for agent in updated.agents))
    existing = next((candidate for candidate in tool_manager.registered_tools if candidate.name == TOOL_ID), None)
    # Reassembly may bind a new reader and principal envelope, but cannot silently
    # replace an unrelated tool occupying the host capability name.
    if existing is not None and any(
        getattr(existing, field.name) != getattr(tool, field.name)
        for field in fields(Tool) if field.init and field.name not in {"handler", "allowed_agents"}
    ):
        raise ValueError("conversation observation tool conflicts with registered execution contract")
    tool_manager.register(tool)
    return updated
