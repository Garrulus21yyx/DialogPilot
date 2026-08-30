"""Bad Case 持久化、去重、生命周期和审计的唯一 Owner。

运行链路只能提交脱敏 observation；只有人工补齐根因、期望与真实复现证据后，
记录才可进入修复/回归状态。Registry 不会把线上信号自动提升为 Gold 或 holdout。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple


class BadCaseStatus(str, Enum):
    CANDIDATE = "candidate"
    TRIAGED = "triaged"
    REPRODUCED = "reproduced"
    FIXING = "fixing"
    REGRESSION_PASS = "regression_pass"
    VERIFIED = "verified"
    CLOSED = "closed"
    DUPLICATE = "duplicate"
    NOT_A_BUG = "not_a_bug"
    NEEDS_PRODUCT_DECISION = "needs_product_decision"
    PRIVACY_REJECTED = "privacy_rejected"


class BadCaseSeverity(str, Enum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class BadCaseStage(str, Enum):
    INPUT_SECURITY = "input_security"
    INTENT = "intent"
    ROUTING = "routing"
    RETRIEVAL = "retrieval"
    MEMORY = "memory"
    PLANNING = "planning"
    EXECUTION = "execution"
    TOOL_POLICY = "tool_policy"
    COVERAGE = "coverage"
    SYNTHESIS = "synthesis"
    VERIFICATION = "verification"
    INFRASTRUCTURE = "infrastructure"


LEGAL_TRANSITIONS = {
    BadCaseStatus.CANDIDATE: {
        BadCaseStatus.TRIAGED,
        BadCaseStatus.DUPLICATE,
        BadCaseStatus.NOT_A_BUG,
        BadCaseStatus.NEEDS_PRODUCT_DECISION,
        BadCaseStatus.PRIVACY_REJECTED,
    },
    BadCaseStatus.TRIAGED: {
        BadCaseStatus.REPRODUCED,
        BadCaseStatus.NOT_A_BUG,
        BadCaseStatus.NEEDS_PRODUCT_DECISION,
        BadCaseStatus.PRIVACY_REJECTED,
    },
    BadCaseStatus.REPRODUCED: {BadCaseStatus.FIXING},
    BadCaseStatus.FIXING: {BadCaseStatus.REGRESSION_PASS},
    BadCaseStatus.REGRESSION_PASS: {BadCaseStatus.VERIFIED},
    BadCaseStatus.VERIFIED: {BadCaseStatus.CLOSED},
    # 线上复发由 observation 原子重开，不能继续保持“已关闭”的错误事实。
    BadCaseStatus.CLOSED: {BadCaseStatus.TRIAGED},
    BadCaseStatus.DUPLICATE: set(),
    BadCaseStatus.NOT_A_BUG: set(),
    BadCaseStatus.NEEDS_PRODUCT_DECISION: {BadCaseStatus.TRIAGED},
    BadCaseStatus.PRIVACY_REJECTED: set(),
}


class BadCaseError(Exception):
    """Bad Case 领域失败基类。"""


class BadCaseNotFoundError(BadCaseError):
    pass


class BadCaseTransitionError(BadCaseError):
    def __init__(self, current: BadCaseStatus, target: BadCaseStatus):
        super().__init__(f"illegal badcase transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


class BadCaseContractError(BadCaseError):
    pass


@dataclass(frozen=True)
class BadCase:
    badcase_id: str
    fingerprint: str
    semantic_group_id: str
    source: str
    stage: BadCaseStage
    severity: BadCaseSeverity
    status: BadCaseStatus
    symptom_code: str
    trace_id: str
    request_id: str
    user_ref: str
    sanitized_input: str
    published_response: str
    evidence: Dict[str, Any]
    versions: Dict[str, Any]
    eval_layer: str
    expected_behavior: Dict[str, Any]
    reproduction: Dict[str, Any]
    root_cause: str
    owner_module: str
    linked_case_id: str
    fixed_by_commit: str
    occurrence_count: int
    created_at: str
    first_seen_at: str
    last_seen_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stage"] = self.stage.value
        data["severity"] = self.severity.value
        data["status"] = self.status.value
        return data


class BadCaseRegistry:
    """以 SQLite 原子拥有 Bad Case 身份、状态与审计历史。"""

    _SECRET_PATTERNS = (
        (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
        (re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
        (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
        # 字母数字边界避免把 SHA-256 中恰好连续的数字误当手机号/卡号。
        (re.compile(r"(?<![A-Za-z0-9])(?:\+?\d[\d -]{7,}\d)(?![A-Za-z0-9])"), "[REDACTED_PHONE]"),
        (re.compile(r"(?<![A-Za-z0-9])(?:\d[ -]*?){13,19}(?![A-Za-z0-9])"), "[REDACTED_NUMBER]"),
    )
    _PROHIBITED_KEYS = re.compile(
        r"(?i)(authorization|password|secret|api.?key|raw.?candidate|tool.?result|jwt)"
    )
    _EVAL_REQUIREMENTS = {
        "intent": ("intent",),
        "routing": ("owners", "task_ids"),
        "retrieval": ("relevant_ids",),
        "stateful": ("assertions",),
    }
    _SEVERITY_RANK = {
        BadCaseSeverity.P0: 0,
        BadCaseSeverity.P1: 1,
        BadCaseSeverity.P2: 2,
        BadCaseSeverity.P3: 3,
    }

    def __init__(self, database_path: str, *, identity_salt: str = ""):
        self._path = Path(database_path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._identity_salt = str(identity_salt or "").encode("utf-8")
        self._lock = threading.RLock()
        self._initialize()

    def observe(
        self,
        *,
        source: str,
        stage: BadCaseStage,
        severity: BadCaseSeverity,
        symptom_code: str,
        user_id: str,
        sanitized_input: str,
        trace_id: str = "",
        request_id: str = "",
        published_response: str = "",
        evidence: Optional[Mapping[str, Any]] = None,
        versions: Optional[Mapping[str, Any]] = None,
        semantic_group_id: str = "",
    ) -> Tuple[BadCase, bool]:
        """幂等捕获一个脱敏 observation；已关闭问题复发时原子重开。"""
        stage = BadCaseStage(stage)
        severity = BadCaseSeverity(severity)
        source = self._required(source, "source")[:80]
        symptom_code = self._slug(self._required(symptom_code, "symptom_code"), 100)
        safe_input = self.sanitize_text(sanitized_input, max_chars=10000)
        safe_response = self.sanitize_text(published_response, max_chars=12000)
        normalized = self._normalize_for_fingerprint(safe_input)
        fingerprint = hashlib.sha256(
            f"{stage.value}\0{symptom_code}\0{normalized}".encode("utf-8")
        ).hexdigest()
        now = self._now()
        badcase_id = uuid.uuid4().hex
        if not self._identity_salt:
            raise BadCaseContractError("identity_salt is required to observe user data")
        user_ref = hmac.new(
            self._identity_salt,
            str(user_id or "anonymous").encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:20]
        safe_evidence = self._sanitize_value(dict(evidence or {}))
        safe_versions = self._sanitize_value(dict(versions or {}))
        group_id = self._slug(semantic_group_id, 160) if semantic_group_id else f"badcase-{fingerprint[:16]}"

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM bad_cases WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if row is not None:
                current = BadCaseStatus(row["status"])
                next_status = BadCaseStatus.TRIAGED if current is BadCaseStatus.CLOSED else current
                current_severity = BadCaseSeverity(row["severity"])
                next_severity = min(
                    (current_severity, severity), key=lambda item: self._SEVERITY_RANK[item]
                )
                conn.execute(
                    """
                    UPDATE bad_cases SET occurrence_count=occurrence_count+1,
                        status=?, severity=?, trace_id=?, request_id=?, published_response=?,
                        evidence_json=?, versions_json=?, last_seen_at=?, updated_at=?
                    WHERE badcase_id=?
                    """,
                    (
                        next_status.value, next_severity.value, str(trace_id)[:128], str(request_id)[:200],
                        safe_response, self._json(safe_evidence), self._json(safe_versions),
                        now, now, row["badcase_id"],
                    ),
                )
                self._insert_occurrence(
                    conn, row["badcase_id"], source, severity, str(trace_id)[:128],
                    str(request_id)[:200], safe_input, safe_response,
                    safe_evidence, safe_versions, now,
                )
                self._insert_event(
                    conn, row["badcase_id"], current, next_status,
                    "system",
                    f"recurrence observed from {source}" if current is BadCaseStatus.CLOSED
                    else f"duplicate observation from {source}",
                    now,
                )
                updated = conn.execute(
                    "SELECT * FROM bad_cases WHERE badcase_id=?", (row["badcase_id"],)
                ).fetchone()
                return self._row_to_badcase(updated), False

            conn.execute(
                """
                INSERT INTO bad_cases (
                    badcase_id, fingerprint, semantic_group_id, source, stage, severity,
                    status, symptom_code, trace_id, request_id, user_ref,
                    sanitized_input, published_response, evidence_json, versions_json,
                    eval_layer, expected_json, reproduction_json, root_cause,
                    owner_module, linked_case_id, fixed_by_commit, occurrence_count,
                    created_at, first_seen_at, last_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '{}', '{}',
                          '', '', '', '', 1, ?, ?, ?, ?)
                """,
                (
                    badcase_id, fingerprint, group_id, source, stage.value, severity.value,
                    BadCaseStatus.CANDIDATE.value, symptom_code, str(trace_id)[:128],
                    str(request_id)[:200], user_ref, safe_input, safe_response,
                    self._json(safe_evidence), self._json(safe_versions),
                    now, now, now, now,
                ),
            )
            self._insert_event(
                conn, badcase_id, None, BadCaseStatus.CANDIDATE,
                "system", f"observed from {source}", now,
            )
            self._insert_occurrence(
                conn, badcase_id, source, severity, str(trace_id)[:128],
                str(request_id)[:200], safe_input, safe_response,
                safe_evidence, safe_versions, now,
            )
            row = conn.execute(
                "SELECT * FROM bad_cases WHERE badcase_id=?", (badcase_id,)
            ).fetchone()
            return self._row_to_badcase(row), True

    def transition(
        self,
        badcase_id: str,
        target: BadCaseStatus,
        *,
        actor: str,
        note: str = "",
        root_cause: Optional[str] = None,
        owner_module: Optional[str] = None,
        eval_layer: Optional[str] = None,
        expected_behavior: Optional[Mapping[str, Any]] = None,
        reproduction: Optional[Mapping[str, Any]] = None,
        fixed_by_commit: Optional[str] = None,
    ) -> BadCase:
        """原子执行人工生命周期迁移，并校验目标状态所需的正向证据。"""
        target = BadCaseStatus(target)
        actor = self._required(actor, "actor")[:200]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM bad_cases WHERE badcase_id=?", (badcase_id,)
            ).fetchone()
            if row is None:
                raise BadCaseNotFoundError(f"badcase not found: {badcase_id}")
            current = BadCaseStatus(row["status"])
            if current is target:
                return self._row_to_badcase(row)
            if target not in LEGAL_TRANSITIONS[current]:
                raise BadCaseTransitionError(current, target)

            values = {
                "root_cause": self.sanitize_text(root_cause if root_cause is not None else row["root_cause"], 4000),
                "owner_module": self._slug(owner_module if owner_module is not None else row["owner_module"], 240),
                "eval_layer": str(eval_layer if eval_layer is not None else row["eval_layer"]).strip(),
                "expected": self._sanitize_value(
                    dict(expected_behavior) if expected_behavior is not None else self._load_json(row["expected_json"])
                ),
                "reproduction": self._sanitize_value(
                    dict(reproduction) if reproduction is not None else self._load_json(row["reproduction_json"])
                ),
                "fixed_by_commit": self._slug(
                    fixed_by_commit if fixed_by_commit is not None else row["fixed_by_commit"], 80
                ),
                "linked_case_id": str(row["linked_case_id"] or ""),
            }
            self._validate_transition_evidence(target, values)
            now = self._now()
            conn.execute(
                """
                UPDATE bad_cases SET status=?, root_cause=?, owner_module=?, eval_layer=?,
                    expected_json=?, reproduction_json=?, fixed_by_commit=?, updated_at=?
                WHERE badcase_id=?
                """,
                (
                    target.value, values["root_cause"], values["owner_module"],
                    values["eval_layer"], self._json(values["expected"]),
                    self._json(values["reproduction"]), values["fixed_by_commit"],
                    now, badcase_id,
                ),
            )
            self._insert_event(
                conn, badcase_id, current, target, actor,
                self.sanitize_text(note, 1000), now,
            )
            updated = conn.execute(
                "SELECT * FROM bad_cases WHERE badcase_id=?", (badcase_id,)
            ).fetchone()
            return self._row_to_badcase(updated)

    def link_regression(self, badcase_id: str, case_id: str, *, actor: str) -> BadCase:
        """把导出的 regression ID 回写为非权威引用；不改变生命周期状态。"""
        case_id = self._slug(self._required(case_id, "case_id"), 200)
        actor = self._required(actor, "actor")[:200]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM bad_cases WHERE badcase_id=?", (badcase_id,)
            ).fetchone()
            if row is None:
                raise BadCaseNotFoundError(f"badcase not found: {badcase_id}")
            if BadCaseStatus(row["status"]) not in {
                BadCaseStatus.REGRESSION_PASS, BadCaseStatus.VERIFIED, BadCaseStatus.CLOSED,
            }:
                raise BadCaseContractError("only regression_pass or later can be exported")
            existing = str(row["linked_case_id"] or "")
            if existing and existing != case_id:
                raise BadCaseContractError("badcase already linked to another regression case")
            now = self._now()
            conn.execute(
                "UPDATE bad_cases SET linked_case_id=?, updated_at=? WHERE badcase_id=?",
                (case_id, now, badcase_id),
            )
            self._insert_event(
                conn, badcase_id, BadCaseStatus(row["status"]), BadCaseStatus(row["status"]),
                actor, f"linked regression case {case_id}", now,
            )
            updated = conn.execute(
                "SELECT * FROM bad_cases WHERE badcase_id=?", (badcase_id,)
            ).fetchone()
            return self._row_to_badcase(updated)

    def get(self, badcase_id: str) -> BadCase:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bad_cases WHERE badcase_id=?", (badcase_id,)
            ).fetchone()
        if row is None:
            raise BadCaseNotFoundError(f"badcase not found: {badcase_id}")
        return self._row_to_badcase(row)

    def get_view(self, badcase_id: str) -> Dict[str, Any]:
        case = self.get(badcase_id)
        with self._connect() as conn:
            events = conn.execute(
                "SELECT * FROM bad_case_events WHERE badcase_id=? ORDER BY event_id",
                (badcase_id,),
            ).fetchall()
            occurrences = conn.execute(
                "SELECT * FROM bad_case_occurrences WHERE badcase_id=? ORDER BY occurrence_id",
                (badcase_id,),
            ).fetchall()
        view = case.to_dict()
        view["events"] = [dict(event) for event in events]
        view["occurrences"] = [
            {
                **dict(item),
                "evidence": self._load_json(item["evidence_json"]),
                "versions": self._load_json(item["versions_json"]),
            }
            for item in occurrences
        ]
        for item in view["occurrences"]:
            item.pop("evidence_json", None)
            item.pop("versions_json", None)
        return view

    def list(
        self,
        *,
        status: Optional[BadCaseStatus] = None,
        stage: Optional[BadCaseStage] = None,
        severity: Optional[BadCaseSeverity] = None,
        limit: int = 50,
    ) -> List[BadCase]:
        clauses: List[str] = []
        params: List[Any] = []
        for column, value, enum_type in (
            ("status", status, BadCaseStatus),
            ("stage", stage, BadCaseStage),
            ("severity", severity, BadCaseSeverity),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(enum_type(value).value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM bad_cases{where} ORDER BY last_seen_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._row_to_badcase(row) for row in rows]

    def stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM bad_cases").fetchone()[0])
            by_status = {row[0]: int(row[1]) for row in conn.execute(
                "SELECT status, COUNT(*) FROM bad_cases GROUP BY status"
            ).fetchall()}
            recurrences = int(conn.execute(
                "SELECT COALESCE(SUM(occurrence_count - 1), 0) FROM bad_cases"
            ).fetchone()[0])
        return {"total": total, "by_status": by_status, "recurrences": recurrences}

    @classmethod
    def sanitize_text(cls, value: Any, max_chars: int = 10000) -> str:
        text = unicodedata.normalize("NFKC", str(value or ""))
        for pattern, replacement in cls._SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        return text[:max(0, int(max_chars))]

    def _validate_transition_evidence(self, target: BadCaseStatus, values: Dict[str, Any]) -> None:
        if target in {
            BadCaseStatus.REPRODUCED, BadCaseStatus.FIXING,
            BadCaseStatus.REGRESSION_PASS, BadCaseStatus.VERIFIED, BadCaseStatus.CLOSED,
        }:
            if not values["root_cause"] or not values["owner_module"]:
                raise BadCaseContractError("root_cause and owner_module are required")
            layer = values["eval_layer"]
            if layer not in self._EVAL_REQUIREMENTS:
                raise BadCaseContractError("eval_layer must be intent/routing/retrieval/stateful")
            missing = [key for key in self._EVAL_REQUIREMENTS[layer] if key not in values["expected"]]
            if missing:
                raise BadCaseContractError(f"expected_behavior missing {missing}")
            if not values["reproduction"]:
                raise BadCaseContractError("real reproduction evidence is required")
            reproduction_required = ("fixture", "owner", "assertions", "evidence_sha256")
            reproduction_missing = [
                key for key in reproduction_required if not values["reproduction"].get(key)
            ]
            if reproduction_missing:
                raise BadCaseContractError(
                    f"reproduction evidence missing {reproduction_missing}"
                )
            if not re.fullmatch(
                r"[0-9a-f]{64}",
                str(values["reproduction"].get("evidence_sha256") or "").lower(),
            ):
                raise BadCaseContractError("reproduction evidence_sha256 must be SHA-256")
        if target in {
            BadCaseStatus.REGRESSION_PASS, BadCaseStatus.VERIFIED, BadCaseStatus.CLOSED,
        } and not values["fixed_by_commit"]:
            raise BadCaseContractError("fixed_by_commit is required")
        if target in {BadCaseStatus.VERIFIED, BadCaseStatus.CLOSED} and not values["linked_case_id"]:
            raise BadCaseContractError("linked regression case is required before verification")

    def _sanitize_value(self, value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "[TRUNCATED_DEPTH]"
        if isinstance(value, Mapping):
            clean: Dict[str, Any] = {}
            for key, item in list(value.items())[:64]:
                safe_key = str(key)[:100]
                if self._PROHIBITED_KEYS.search(safe_key):
                    clean[safe_key] = "[REDACTED]"
                else:
                    clean[safe_key] = self._sanitize_value(item, depth + 1)
            return clean
        if isinstance(value, (list, tuple, set)):
            return [self._sanitize_value(item, depth + 1) for item in list(value)[:100]]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return self.sanitize_text(value, 2000)

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bad_cases (
                    badcase_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    semantic_group_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    symptom_code TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    user_ref TEXT NOT NULL,
                    sanitized_input TEXT NOT NULL,
                    published_response TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    versions_json TEXT NOT NULL,
                    eval_layer TEXT NOT NULL,
                    expected_json TEXT NOT NULL,
                    reproduction_json TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    owner_module TEXT NOT NULL,
                    linked_case_id TEXT NOT NULL,
                    fixed_by_commit TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bad_cases_queue
                    ON bad_cases(status, severity, last_seen_at);
                CREATE TABLE IF NOT EXISTS bad_case_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    badcase_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(badcase_id) REFERENCES bad_cases(badcase_id)
                );
                CREATE TABLE IF NOT EXISTS bad_case_occurrences (
                    occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    badcase_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    sanitized_input TEXT NOT NULL,
                    published_response TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    versions_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    FOREIGN KEY(badcase_id) REFERENCES bad_cases(badcase_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bad_case_occurrences
                    ON bad_case_occurrences(badcase_id, occurrence_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        badcase_id: str,
        current: Optional[BadCaseStatus],
        target: BadCaseStatus,
        actor: str,
        note: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO bad_case_events (
                badcase_id, from_status, to_status, actor, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                badcase_id, current.value if current else None, target.value,
                actor, note, created_at,
            ),
        )

    @classmethod
    def _insert_occurrence(
        cls,
        conn: sqlite3.Connection,
        badcase_id: str,
        source: str,
        severity: BadCaseSeverity,
        trace_id: str,
        request_id: str,
        sanitized_input: str,
        published_response: str,
        evidence: Dict[str, Any],
        versions: Dict[str, Any],
        observed_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO bad_case_occurrences (
                badcase_id, source, severity, trace_id, request_id,
                sanitized_input, published_response, evidence_json,
                versions_json, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                badcase_id, source, severity.value, trace_id, request_id,
                sanitized_input, published_response, cls._json(evidence),
                cls._json(versions), observed_at,
            ),
        )

    @classmethod
    def _row_to_badcase(cls, row: sqlite3.Row) -> BadCase:
        return BadCase(
            badcase_id=row["badcase_id"], fingerprint=row["fingerprint"],
            semantic_group_id=row["semantic_group_id"], source=row["source"],
            stage=BadCaseStage(row["stage"]), severity=BadCaseSeverity(row["severity"]),
            status=BadCaseStatus(row["status"]), symptom_code=row["symptom_code"],
            trace_id=row["trace_id"], request_id=row["request_id"], user_ref=row["user_ref"],
            sanitized_input=row["sanitized_input"], published_response=row["published_response"],
            evidence=cls._load_json(row["evidence_json"]),
            versions=cls._load_json(row["versions_json"]),
            eval_layer=row["eval_layer"], expected_behavior=cls._load_json(row["expected_json"]),
            reproduction=cls._load_json(row["reproduction_json"]),
            root_cause=row["root_cause"], owner_module=row["owner_module"],
            linked_case_id=row["linked_case_id"], fixed_by_commit=row["fixed_by_commit"],
            occurrence_count=int(row["occurrence_count"]), created_at=row["created_at"],
            first_seen_at=row["first_seen_at"], last_seen_at=row["last_seen_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _required(value: Any, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise BadCaseContractError(f"{field} is required")
        return text

    @staticmethod
    def _slug(value: Any, limit: int) -> str:
        return re.sub(r"[^A-Za-z0-9._:/-]+", "-", str(value or "").strip()).strip("-")[:limit]

    @staticmethod
    def _normalize_for_fingerprint(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _load_json(value: str) -> Dict[str, Any]:
        loaded = json.loads(value or "{}")
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
