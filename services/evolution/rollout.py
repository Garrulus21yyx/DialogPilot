"""Shadow/Canary/Active 发布状态机、稳定分桶和自动回滚。"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from evaluation.graduation import GraduationDecision
from .bundle import AgentBundle
from .registry import AgentBundleRegistry, BundleNotFoundError


class RolloutContractError(RuntimeError):
    pass


class RolloutState(str, Enum):
    CANDIDATE = "candidate"
    SHADOW = "shadow"
    CANARY = "canary"
    ACTIVE = "active"
    RETIRED = "retired"
    ROLLED_BACK = "rolled_back"


class HardSignal(str, Enum):
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    PRIVACY_LEAK = "privacy_leak"
    CROSS_USER_RETRIEVAL = "cross_user_retrieval"
    WRONG_WRITE = "wrong_write"


@dataclass(frozen=True)
class RolloutAssignment:
    primary: AgentBundle
    primary_stage: str
    shadow: Optional[AgentBundle] = None
    bucket: int = 0


@dataclass(frozen=True)
class SoftRollbackPolicy:
    min_candidate_samples: int = 100
    min_baseline_samples: int = 100
    max_reject_rate_delta: float = 0.05
    max_latency_p95_ratio: float = 1.20
    max_cost_mean_ratio: float = 1.20
    confidence_z: float = 1.96
    sample_window: int = 1000


class RolloutManager:
    """Rollout 状态和 Bundle 指针的唯一写 Owner。"""

    _LEGAL = {
        RolloutState.CANDIDATE: {RolloutState.SHADOW},
        RolloutState.SHADOW: {RolloutState.CANARY, RolloutState.ROLLED_BACK},
        RolloutState.CANARY: {RolloutState.CANARY, RolloutState.ACTIVE, RolloutState.ROLLED_BACK},
        RolloutState.ACTIVE: {RolloutState.RETIRED, RolloutState.ROLLED_BACK},
        RolloutState.RETIRED: {RolloutState.ACTIVE},
        RolloutState.ROLLED_BACK: set(),
    }

    def __init__(
        self,
        registry: AgentBundleRegistry,
        *,
        bucket_salt: str,
        activation_validator: Optional[Callable[[AgentBundle], Tuple[str, ...]]] = None,
        soft_policy: Optional[SoftRollbackPolicy] = None,
    ):
        if not str(bucket_salt):
            raise RolloutContractError("rollout bucket_salt is required")
        self._registry = registry
        self._path = registry.db_path
        self._salt = str(bucket_salt)
        self._validator = activation_validator or (lambda bundle: ())
        self._soft = soft_policy or SoftRollbackPolicy()
        self._lock = threading.RLock()
        self._initialize()
        self._bootstrap_active()

    def start_shadow(
        self,
        version: str,
        *,
        graduation: GraduationDecision,
        actor: str,
    ) -> Dict[str, Any]:
        if not graduation.graduated or graduation.candidate_id != version:
            raise RolloutContractError("shadow requires matching graduated evidence")
        candidate = self._registry.get(version)
        errors = tuple(self._validator(candidate))
        if errors:
            raise RolloutContractError(f"activation validation failed: {list(errors)}")
        active = self._registry.active()
        if candidate.version == active.version:
            raise RolloutContractError("active bundle cannot start a shadow rollout")
        decision_hash = self._hash(graduation.to_dict())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT version FROM bundle_pointers WHERE name='shadow'"
            ).fetchone()
            if existing and existing["version"] != version:
                raise RolloutContractError(f"another shadow rollout is active: {existing['version']}")
            self._ensure_candidate(conn, candidate.version, active.version)
            self._transition(conn, candidate.version, RolloutState.SHADOW, actor, {
                "graduation_hash": decision_hash,
                "baseline_version": active.version,
            })
            conn.execute(
                "UPDATE rollouts SET baseline_version=?, graduation_hash=?, canary_percent=0, updated_at=? "
                "WHERE bundle_version=?",
                (active.version, decision_hash, self._now(), candidate.version),
            )
            self._set_pointer(conn, "shadow", candidate.version, actor)
        return self.status(candidate.version)

    def promote_canary(self, version: str, *, percent: int, actor: str) -> Dict[str, Any]:
        percent = int(percent)
        if percent not in {5, 25}:
            raise RolloutContractError("supported canary stages are 5 and 25 percent")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._rollout_row(conn, version)
            state = RolloutState(row["state"])
            prior_percent = int(row["canary_percent"])
            if state is RolloutState.CANARY and percent <= prior_percent:
                raise RolloutContractError("canary percentage must increase monotonically")
            self._transition(conn, version, RolloutState.CANARY, actor, {"percent": percent})
            conn.execute(
                "UPDATE rollouts SET canary_percent=?, updated_at=? WHERE bundle_version=?",
                (percent, self._now(), version),
            )
            self._delete_pointer(conn, "shadow")
            self._set_pointer(conn, "canary", version, actor)
        return self.status(version)

    def promote_active(self, version: str, *, actor: str) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._rollout_row(conn, version)
            if RolloutState(row["state"]) is not RolloutState.CANARY:
                raise RolloutContractError("only a canary can become active")
            if int(row["canary_percent"]) < 25:
                raise RolloutContractError("active promotion requires successful 25% canary")
            previous = conn.execute(
                "SELECT version FROM bundle_pointers WHERE name='active'"
            ).fetchone()
            if previous and previous["version"] != version:
                self._ensure_active_row(conn, previous["version"])
                self._transition(
                    conn, previous["version"], RolloutState.RETIRED, actor,
                    {"replaced_by": version},
                )
            self._transition(conn, version, RolloutState.ACTIVE, actor, {})
            self._set_pointer(conn, "active", version, actor)
            self._delete_pointer(conn, "canary")
            conn.execute(
                "UPDATE rollouts SET canary_percent=0, updated_at=? WHERE bundle_version=?",
                (self._now(), version),
            )
        return self.status(version)

    def resolve(self, subject_key: str) -> RolloutAssignment:
        """稳定用户分桶；返回对象即请求生命周期内不可变的版本快照。"""
        active = self._registry.active()
        canary = self._registry.pointer("canary")
        shadow = self._registry.pointer("shadow")
        bucket = self._bucket(subject_key)
        if canary is not None:
            status = self.status(canary.version)
            if bucket < int(status["canary_percent"]) * 100:
                return RolloutAssignment(canary, "canary", shadow, bucket)
        return RolloutAssignment(active, "active", shadow, bucket)

    def record_outcome(
        self,
        *,
        bundle_version: str,
        stage: str,
        verified: bool,
        latency_ms: float,
        cost_units: float = 0.0,
        request_id: str = "",
        hard_signal: str = "",
    ) -> Optional[Dict[str, Any]]:
        signal = HardSignal(hard_signal) if hard_signal else None
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO rollout_metrics(bundle_version, stage, verified, latency_ms, cost_units, "
                "request_id, hard_signal, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bundle_version, str(stage)[:20], int(bool(verified)),
                    max(0.0, float(latency_ms)), max(0.0, float(cost_units)),
                    str(request_id)[:160], signal.value if signal else "", self._now(),
                ),
            )
            if signal is not None:
                return self._rollback_locked(
                    conn, bundle_version, actor="automatic-hard-signal",
                    reason={"hard_signal": signal.value, "request_id": str(request_id)[:160]},
                )
        return self.evaluate_soft_rollback(bundle_version)

    def evaluate_soft_rollback(self, version: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._rollout_row(conn, version)
            state = RolloutState(row["state"])
            if state not in {RolloutState.CANARY, RolloutState.ACTIVE}:
                return None
            baseline = str(row["baseline_version"] or "")
            if not baseline:
                return None
            candidate_metrics = self._metrics(conn, version)
            baseline_metrics = self._metrics(conn, baseline)
            reason = self._soft_reason(candidate_metrics, baseline_metrics)
            if reason is None:
                return None
            return self._rollback_locked(
                conn, version, actor="automatic-soft-signal", reason=reason,
            )

    def rollback(self, version: str, *, actor: str, reason: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self._rollback_locked(conn, version, actor=actor, reason=dict(reason))
            if result is None:
                raise RolloutContractError("bundle is not in a rollback-capable state")
            return result

    def status(self, version: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = self._rollout_row(conn, version)
            events = conn.execute(
                "SELECT from_state, to_state, actor, evidence_json, observed_at "
                "FROM rollout_events WHERE bundle_version=? ORDER BY event_id",
                (version,),
            ).fetchall()
        return {
            **dict(row),
            "events": [
                {**dict(event), "evidence": json.loads(event["evidence_json"])}
                for event in events
            ],
        }

    def _rollback_locked(
        self,
        conn: sqlite3.Connection,
        version: str,
        *,
        actor: str,
        reason: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        row = self._rollout_row(conn, version)
        state = RolloutState(row["state"])
        if state not in {RolloutState.SHADOW, RolloutState.CANARY, RolloutState.ACTIVE}:
            return None
        baseline = str(row["baseline_version"] or "")
        self._transition(conn, version, RolloutState.ROLLED_BACK, actor, reason)
        self._delete_pointer(conn, "shadow", expected=version)
        self._delete_pointer(conn, "canary", expected=version)
        active = conn.execute("SELECT version FROM bundle_pointers WHERE name='active'").fetchone()
        if state is RolloutState.ACTIVE and baseline:
            if active and active["version"] == version:
                self._ensure_active_row(conn, baseline)
                baseline_row = self._rollout_row(conn, baseline)
                baseline_state = RolloutState(baseline_row["state"])
                if baseline_state is not RolloutState.ACTIVE:
                    self._transition(conn, baseline, RolloutState.ACTIVE, actor, {
                        "restored_from": version,
                    })
                self._set_pointer(conn, "active", baseline, actor)
        return {
            "rolled_back": version,
            "active_version": self._pointer_version(conn, "active"),
            "reason": dict(reason),
        }

    def _soft_reason(self, candidate: Dict[str, Any], baseline: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if candidate["count"] < self._soft.min_candidate_samples:
            return None
        if baseline["count"] < self._soft.min_baseline_samples:
            return None
        cand_interval = self._wilson(candidate["passed"], candidate["count"])
        base_interval = self._wilson(baseline["passed"], baseline["count"])
        pass_delta = baseline["pass_rate"] - candidate["pass_rate"]
        if pass_delta > self._soft.max_reject_rate_delta and base_interval[0] > cand_interval[1]:
            return {"soft_signal": "verified_pass_rate", "candidate": candidate, "baseline": baseline}
        if baseline["latency_p95"] > 0 and (
            candidate["latency_p95"] / baseline["latency_p95"]
        ) > self._soft.max_latency_p95_ratio:
            return {"soft_signal": "latency_p95", "candidate": candidate, "baseline": baseline}
        if baseline["cost_mean"] > 0 and (
            candidate["cost_mean"] / baseline["cost_mean"]
        ) > self._soft.max_cost_mean_ratio:
            return {"soft_signal": "cost_mean", "candidate": candidate, "baseline": baseline}
        return None

    def _metrics(self, conn: sqlite3.Connection, version: str) -> Dict[str, Any]:
        rows = conn.execute(
            "SELECT verified, latency_ms, cost_units FROM rollout_metrics "
            "WHERE bundle_version=? ORDER BY metric_id DESC LIMIT ?",
            (version, self._soft.sample_window),
        ).fetchall()
        latencies = sorted(float(row["latency_ms"]) for row in rows)
        costs = [float(row["cost_units"]) for row in rows]
        count = len(rows)
        passed = sum(int(row["verified"]) for row in rows)
        p95_index = max(0, math.ceil(count * 0.95) - 1) if count else 0
        return {
            "count": count,
            "passed": passed,
            "pass_rate": passed / count if count else 0.0,
            "latency_p95": latencies[p95_index] if latencies else 0.0,
            "cost_mean": sum(costs) / count if count else 0.0,
        }

    def _wilson(self, passed: int, total: int) -> Tuple[float, float]:
        if total <= 0:
            return (0.0, 1.0)
        z = self._soft.confidence_z
        p = passed / total
        denominator = 1 + z * z / total
        centre = (p + z * z / (2 * total)) / denominator
        margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
        return max(0.0, centre - margin), min(1.0, centre + margin)

    def _transition(
        self,
        conn: sqlite3.Connection,
        version: str,
        target: RolloutState,
        actor: str,
        evidence: Mapping[str, Any],
    ) -> None:
        row = self._rollout_row(conn, version)
        current = RolloutState(row["state"])
        if target not in self._LEGAL[current]:
            raise RolloutContractError(f"illegal rollout transition: {current.value} -> {target.value}")
        now = self._now()
        conn.execute(
            "UPDATE rollouts SET state=?, updated_at=? WHERE bundle_version=?",
            (target.value, now, version),
        )
        conn.execute(
            "INSERT INTO rollout_events(bundle_version, from_state, to_state, actor, evidence_json, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (version, current.value, target.value, str(actor)[:200], self._json(evidence), now),
        )

    def _ensure_candidate(self, conn: sqlite3.Connection, version: str, baseline: str) -> None:
        now = self._now()
        conn.execute(
            "INSERT OR IGNORE INTO rollouts(bundle_version, state, baseline_version, canary_percent, "
            "graduation_hash, created_at, updated_at) VALUES (?, ?, ?, 0, '', ?, ?)",
            (version, RolloutState.CANDIDATE.value, baseline, now, now),
        )

    def _ensure_active_row(self, conn: sqlite3.Connection, version: str) -> None:
        now = self._now()
        conn.execute(
            "INSERT OR IGNORE INTO rollouts(bundle_version, state, baseline_version, canary_percent, "
            "graduation_hash, created_at, updated_at) VALUES (?, ?, '', 0, 'bootstrap', ?, ?)",
            (version, RolloutState.ACTIVE.value, now, now),
        )

    def _bootstrap_active(self) -> None:
        active = self._registry.active()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_active_row(conn, active.version)

    def _rollout_row(self, conn: sqlite3.Connection, version: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM rollouts WHERE bundle_version=?", (version,)).fetchone()
        if row is None:
            raise RolloutContractError(f"rollout not found: {version}")
        return row

    def _set_pointer(self, conn: sqlite3.Connection, name: str, version: str, actor: str) -> None:
        conn.execute(
            "INSERT INTO bundle_pointers(name, version, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at, "
            "updated_by=excluded.updated_by",
            (name, version, self._now(), str(actor)[:200]),
        )

    @staticmethod
    def _delete_pointer(conn: sqlite3.Connection, name: str, expected: str = "") -> None:
        if expected:
            conn.execute("DELETE FROM bundle_pointers WHERE name=? AND version=?", (name, expected))
        else:
            conn.execute("DELETE FROM bundle_pointers WHERE name=?", (name,))

    @staticmethod
    def _pointer_version(conn: sqlite3.Connection, name: str) -> str:
        row = conn.execute("SELECT version FROM bundle_pointers WHERE name=?", (name,)).fetchone()
        return str(row["version"]) if row else ""

    def _bucket(self, subject_key: str) -> int:
        digest = hashlib.sha256(f"{self._salt}\0{subject_key}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % 10000

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rollouts (
                    bundle_version TEXT PRIMARY KEY REFERENCES agent_bundles(version),
                    state TEXT NOT NULL,
                    baseline_version TEXT NOT NULL,
                    canary_percent INTEGER NOT NULL,
                    graduation_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rollout_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bundle_version TEXT NOT NULL REFERENCES agent_bundles(version),
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rollout_metrics (
                    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bundle_version TEXT NOT NULL REFERENCES agent_bundles(version),
                    stage TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    cost_units REAL NOT NULL,
                    request_id TEXT NOT NULL,
                    hard_signal TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rollout_metrics_version
                    ON rollout_metrics(bundle_version, metric_id DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _hash(value: Mapping[str, Any]) -> str:
        return hashlib.sha256(RolloutManager._json(value).encode("utf-8")).hexdigest()
