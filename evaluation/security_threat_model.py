"""Machine validation for the bounded X-T03 threat/control inventory."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class SecurityThreatModelError(ValueError):
    pass


REQUIRED_THREATS = frozenset({
    "USER_PROMPT_INJECTION",
    "INDIRECT_CONTEXT_INJECTION",
    "CROSS_TENANT_ACCESS",
    "APPROVAL_PRIVILEGE_REPLAY",
    "CHECKPOINT_TRACE_DISCLOSURE",
    "ATTACHMENT_POLYGLOT",
    "VLM_HIDDEN_INSTRUCTION",
    "WRITE_EFFECT_REPLAY",
})


@dataclass(frozen=True)
class SecurityThreatModel:
    model_id: str
    assets: tuple[Mapping[str, Any], ...]
    trust_boundaries: tuple[Mapping[str, Any], ...]
    threats: tuple[Mapping[str, Any], ...]

    def validate(self) -> None:
        if not self.model_id.strip() or not self.assets or not self.trust_boundaries:
            raise SecurityThreatModelError("threat model identity/topology is incomplete")
        ids = [str(item.get("threat_id") or "") for item in self.threats]
        if len(ids) != len(set(ids)) or set(ids) != REQUIRED_THREATS:
            raise SecurityThreatModelError("threat inventory is incomplete or duplicated")
        for threat in self.threats:
            required = (
                "threat_id", "surface", "asset", "owner", "applicability",
                "abuse_case", "disable_action", "residual_risk",
            )
            if any(not str(threat.get(key) or "").strip() for key in required):
                raise SecurityThreatModelError("threat fields are incomplete")
            applicability = threat["applicability"]
            controls = tuple(threat.get("controls") or ())
            if applicability == "ACTIVE":
                kinds = {str(item.get("kind") or "") for item in controls}
                if "PREVENT" not in kinds or "DETECT" not in kinds:
                    raise SecurityThreatModelError(
                        f"active threat lacks prevent/detect controls: {threat['threat_id']}"
                    )
                if any(
                    not item.get("control_id") or not item.get("owner")
                    or not item.get("evidence_refs")
                    for item in controls
                ):
                    raise SecurityThreatModelError("control evidence is incomplete")
            elif applicability == "NOT_APPLICABLE":
                if not threat.get("na_reason") or not threat.get("activation_owner"):
                    raise SecurityThreatModelError("N/A threat lacks activation boundary")
            else:
                raise SecurityThreatModelError("unknown threat applicability")


def load_threat_model(path: str | Path) -> SecurityThreatModel:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(raw) != {"model_id", "assets", "trust_boundaries", "threats"}:
        raise SecurityThreatModelError("unknown or missing top-level threat model fields")
    model = SecurityThreatModel(
        model_id=str(raw["model_id"]), assets=tuple(raw["assets"]),
        trust_boundaries=tuple(raw["trust_boundaries"]),
        threats=tuple(raw["threats"]),
    )
    model.validate()
    return model
