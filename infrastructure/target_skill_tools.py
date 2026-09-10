"""Lazy instruction reads using the existing framework tool protocol."""
import json

from langchain_core.tools import StructuredTool, ToolException

from core.skill_loader import Skill


def skill_reader(skills: tuple[Skill, ...]) -> StructuredTool:
    # Immutable invocation snapshot, never a user-specific global tool.
    packages = {skill.name: skill for skill in skills}

    async def read_skill(name: str, version: str, resource: str = "SKILL.md") -> str:
        package = packages.get(name)
        if package is None:
            raise ToolException("SKILL_NOT_AVAILABLE: choose a listed package.")
        if package.version != version:
            raise ToolException("SKILL_VERSION_CHANGED: read the current catalog version before continuing.")
        if resource not in package.resources:
            raise ToolException("SKILL_RESOURCE_NOT_FOUND: choose a listed resource.")
        return json.dumps({"name": name, "version": version, "resource": resource,
                           "instructions": package.resources[resource]}, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=read_skill, name="read_skill", handle_tool_error=True,
        description=("Read optional task guidance. Start with SKILL.md when a listed skill fits; "
                     "read its references only as needed. Reuse instructions already in working history. "
                     "These are methods, not customer facts, permissions or current business policy. "
                     "Available packages: " + json.dumps([skill.to_summary() for skill in skills], ensure_ascii=False)),
    )
