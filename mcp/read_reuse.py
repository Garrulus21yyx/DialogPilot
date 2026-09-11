"""Tool-owned reuse lifetime for private task snapshots (not a global cache)."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math


@dataclass(frozen=True)
class ReadReusePolicy:
    # None is only for environments whose reads remain stable until a governed
    # mutation. Live services should declare their tolerated maximum age.
    max_age_seconds: float | None

    def __post_init__(self):
        if self.max_age_seconds is not None and (
                not math.isfinite(self.max_age_seconds) or self.max_age_seconds <= 0):
            raise ValueError("read reuse age must be finite and positive")

    def valid(self, record, *, epoch, now=None):
        if record.get("epoch") != epoch:
            return False
        now = now or datetime.now(timezone.utc)
        observed = datetime.fromisoformat(record["observed_at"])
        age = (now - observed).total_seconds()
        return age >= 0 and (self.max_age_seconds is None or age < self.max_age_seconds)


def read_key(tool, arguments, trusted_context, registry_fingerprint=""):
    # Also bind identity-injected schemas and implementation/output versions.
    value = [registry_fingerprint, tool.name, tool.authority, tool.manifest_version, tool.output_schema_version,
             tool.task_read_reuse.max_age_seconds if tool.task_read_reuse else "disabled",
             tool.input_schema(trusted_context), arguments,
             [trusted_context.get(key) for key in ("tenant_id", "user_id", "conversation_id")]]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def merge_read_records(left, right):
    """Parallel results converge independently of reducer arrival order."""
    merged = dict(left)
    for key, record in right.items():
        old = merged.get(key)
        if old is None or (datetime.fromisoformat(record["observed_at"]), record["reference"]) > (
                datetime.fromisoformat(old["observed_at"]), old["reference"]):
            merged[key] = record
    return merged
