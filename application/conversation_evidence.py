"""Historical knowledge provenance, distinct from conversation text or new work."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationEvidence:
    knowledge: tuple[dict, ...] = ()
    business: tuple[dict, ...] = ()


def evidence_identity(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry["pack"], ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def reusable_evidence(context) -> tuple[dict, ...]:
    """Only source-authorized entries from the trusted context loader are reusable.

    CURRENT describes source lifetime, not applicability to a new user objective.
    Scope and the original query stay on the pack for the agent/support verifier.
    """
    return tuple({"pack": entry["pack"], "observed_at": entry["observed_at"]}
                 for entry in (context or {}).get("knowledge_evidence", ())
                 if entry.get("status") == "CURRENT")
