"""Discover versioned instruction packages; never inject keyword-matched prompts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(min_length=1)
    agents: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    agents: tuple[str, ...]
    version: str
    resources: Mapping[str, str]

    def to_summary(self) -> dict:
        return {"name": self.name, "description": self.description,
                "agents": list(self.agents), "version": self.version,
                "resources": list(self.resources)}


class SkillManager:
    """Shared catalog. Reload replaces the snapshot atomically or fails explicitly."""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir).expanduser().resolve()
        self._skills: tuple[Skill, ...] = ()

    @property
    def skills(self) -> tuple[Skill, ...]:
        return self._skills

    def for_agent(self, agent_id: str) -> tuple[Skill, ...]:
        return tuple(skill for skill in self._skills if agent_id in skill.agents)

    def load(self) -> tuple[Skill, ...]:
        loaded = tuple(self._load(path) for path in sorted(self.root_dir.glob("*/SKILL.md")))
        if len({skill.name for skill in loaded}) != len(loaded):
            raise ValueError("DUPLICATE_SKILL_NAME")
        self._skills = loaded
        return loaded

    def reload(self) -> tuple[Skill, ...]:
        return self.load()

    def summary(self) -> dict:
        return {"root_dir": str(self.root_dir), "count": len(self._skills),
                "skills": [skill.to_summary() for skill in self._skills]}

    def _load(self, path: Path) -> Skill:
        root = path.parent.resolve()
        if not root.is_relative_to(self.root_dir) or not path.resolve().is_relative_to(root):
            raise ValueError("SKILL_RESOURCE_OUTSIDE_PACKAGE")
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "---":
            raise ValueError(f"SKILL_METADATA_REQUIRED: {path}")
        end = lines.index("---", 1)
        try:
            metadata = yaml.safe_load("\n".join(lines[1:end]))
        except yaml.YAMLError as exc:
            raise ValueError("SKILL_METADATA_INVALID_YAML") from exc
        meta = SkillMetadata.model_validate(metadata)
        body = "\n".join(lines[end + 1:]).strip()
        if not body:
            raise ValueError("SKILL_BODY_REQUIRED")
        resources = {"SKILL.md": body}
        for directory in ("references", "examples"):
            for resource in sorted((root / directory).rglob("*.md")):
                if not resource.resolve().is_relative_to(root):
                    raise ValueError("SKILL_RESOURCE_OUTSIDE_PACKAGE")
                resources[resource.relative_to(root).as_posix()] = resource.read_text(encoding="utf-8")
        fingerprint = json.dumps({"metadata": meta.model_dump(), "resources": resources},
                                 ensure_ascii=False, sort_keys=True).encode()
        return Skill(meta.name, meta.description, tuple(meta.agents),
                     hashlib.sha256(fingerprint).hexdigest(), MappingProxyType(resources))
