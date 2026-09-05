"""Bad Case 持久化、去重、生命周期和审计的唯一 Owner。

运行链路只能提交脱敏 observation；只有人工补齐根因、期望与真实复现证据后，
记录才可进入修复/回归状态。Registry 不会把线上信号自动提升为 Gold 或 holdout。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional, Tuple

from psycopg import Cursor
from psycopg.rows import dict_row

if TYPE_CHECKING:
    from infrastructure.postgres import PostgresPool


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


class IntentFeedbackStatus(str, Enum):
    """一次不可变预测在离线学习链中的闭合状态。"""

    PREDICTED = "predicted"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class IntentLearningRecord:
    """预测事实、候选纠正和人工裁决的单一聚合投影。"""

    prediction_id: str
    request_id: str
    trace_id: str
    conv_id: str
    user_ref: str
    input_fingerprint: str
    sanitized_input: str
    predicted_intent: str
    confidence: float
    source_scores: Dict[str, float]
    classifier_fingerprint: str
    bundle_version: str
    feedback_status: IntentFeedbackStatus
    suggested_intent: str
    feedback_reason: str
    badcase_id: str
    approved_intent: str
    annotation_id: str
    reviewer: str
    dataset_version: str
    review_note: str
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["feedback_status"] = self.feedback_status.value
        return data


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
    prediction_id: str = ""
    predicted_intent: str = ""
    suggested_intent: str = ""
    approved_intent: str = ""
    annotation_id: str = ""
    classifier_fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["stage"] = self.stage.value
        data["severity"] = self.severity.value
        data["status"] = self.status.value
        return data


class BadCaseRegistry:
    """以 PostgreSQL 原子拥有 Bad Case 身份、状态与审计历史。"""

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

    def __init__(self, pool: PostgresPool, *, identity_salt: str = ""):
        self.pool = pool
        self._identity_salt = str(identity_salt or "").encode("utf-8")

    @contextmanager
    def _connect(self) -> Iterator[Cursor]:
        with (
            self.pool.transaction() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            yield cursor

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
        with self._connect() as conn:
            return self._observe_with_connection(
                conn,
                source=source,
                stage=stage,
                severity=severity,
                symptom_code=symptom_code,
                user_id=user_id,
                sanitized_input=sanitized_input,
                trace_id=trace_id,
                request_id=request_id,
                published_response=published_response,
                evidence=evidence,
                versions=versions,
                semantic_group_id=semantic_group_id,
            )

    def record_intent_prediction(
        self,
        *,
        request_id: str,
        trace_id: str,
        conv_id: str,
        user_id: str,
        input_fingerprint: str,
        sanitized_input: str,
        predicted_intent: str,
        confidence: float,
        source_scores: Mapping[str, Any],
        classifier_fingerprint: str,
        bundle_version: str,
    ) -> IntentLearningRecord:
        """追加一次不可变预测事实；不把预测本身视作反馈或 Gold。"""
        if not self._identity_salt:
            raise BadCaseContractError("identity_salt is required to record intent predictions")
        request_id = self._required(request_id, "request_id")[:200]
        predicted = self._intent_name(predicted_intent, "predicted_intent")
        input_hash = self._sha256(input_fingerprint, "input_fingerprint")
        classifier_hash = self._sha256(
            classifier_fingerprint, "classifier_fingerprint",
        )
        try:
            numeric_confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise BadCaseContractError("confidence must be a number") from exc
        if not math.isfinite(numeric_confidence) or not 0.0 <= numeric_confidence <= 1.0:
            raise BadCaseContractError("confidence must be between 0 and 1")
        safe_scores: Dict[str, float] = {}
        for key, value in list(source_scores.items())[:32]:
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(score):
                safe_scores[self._slug(key, 80)] = min(1.0, max(0.0, score))
        now = self._now()
        prediction_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dialogpilot_platform.intent_learning_records (
                    prediction_id, request_id, trace_id, conv_id, user_ref,
                    input_fingerprint, sanitized_input, predicted_intent,
                    confidence, source_scores_json, classifier_fingerprint,
                    bundle_version, feedback_status, suggested_intent,
                    feedback_reason, badcase_id, approved_intent, annotation_id,
                    reviewer, dataset_version, review_note, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', '', '', '', '', '', '', '', %s, %s)
                """,
                (
                    prediction_id, request_id, str(trace_id)[:128], str(conv_id)[:200],
                    self._user_ref(user_id), input_hash,
                    self.sanitize_text(sanitized_input, 2000), predicted,
                    numeric_confidence, self._json(safe_scores), classifier_hash,
                    self._slug(bundle_version or "unversioned", 128),
                    IntentFeedbackStatus.PREDICTED.value, now, now,
                ),
            )
            self._insert_intent_learning_event(
                conn,
                prediction_id,
                "prediction_recorded",
                "system",
                {
                    "predicted_intent": predicted,
                    "confidence": numeric_confidence,
                    "classifier_fingerprint": classifier_hash,
                },
                now,
            )
            row = conn.execute(
                "SELECT * FROM dialogpilot_platform.intent_learning_records WHERE prediction_id=%s",
                (prediction_id,),
            ).fetchone()
        return self._row_to_intent_learning(row)

    def submit_intent_feedback(
        self,
        *,
        prediction_id: str,
        user_id: str,
        suggested_intent: str,
        reason: str = "",
        published_response: str = "",
        source: str = "user_feedback",
    ) -> Tuple[IntentLearningRecord, BadCase, bool]:
        """把认证用户纠正保存为 pending observation，并原子链接一个 Intent Bad Case。"""
        prediction_id = self._required(prediction_id, "prediction_id")[:64]
        suggested = self._intent_name(suggested_intent, "suggested_intent")
        with self._connect() as conn:
            prediction = conn.execute(
                "SELECT * FROM dialogpilot_platform.intent_learning_records WHERE prediction_id=%s FOR UPDATE",
                (prediction_id,),
            ).fetchone()
            if prediction is None or prediction["user_ref"] != self._user_ref(user_id):
                # 不区分不存在和他人预测，避免枚举用户质量记录。
                raise BadCaseNotFoundError("intent prediction not found")
            current = IntentFeedbackStatus(prediction["feedback_status"])
            if current in {IntentFeedbackStatus.APPROVED, IntentFeedbackStatus.REJECTED}:
                raise BadCaseContractError("reviewed intent feedback is immutable")
            if suggested == prediction["predicted_intent"]:
                raise BadCaseContractError("suggested_intent must differ from predicted_intent")
            if current is IntentFeedbackStatus.PENDING and prediction["suggested_intent"] != suggested:
                raise BadCaseContractError("a prediction cannot have conflicting pending labels")

            safe_reason = self.sanitize_text(reason, 2000)
            evidence = {
                "prediction_id": prediction_id,
                "predicted_intent": prediction["predicted_intent"],
                "suggested_intent": suggested,
                "classifier_fingerprint": prediction["classifier_fingerprint"],
                "confidence": float(prediction["confidence"]),
                "feedback_reason": safe_reason,
            }
            case, created = self._observe_with_connection(
                conn,
                source=source,
                stage=BadCaseStage.INTENT,
                severity=BadCaseSeverity.P2,
                symptom_code=f"wrong-route-{prediction_id}",
                user_id=user_id,
                sanitized_input=prediction["sanitized_input"],
                trace_id=prediction["trace_id"],
                request_id=prediction["request_id"],
                published_response=published_response,
                evidence=evidence,
                versions={
                    "classifier_fingerprint": prediction["classifier_fingerprint"],
                    "agent_bundle_version": prediction["bundle_version"],
                },
                semantic_group_id=(
                    f"intent-{prediction['predicted_intent']}-to-{suggested}"
                ),
            )
            now = self._now()
            conn.execute(
                """
                UPDATE dialogpilot_platform.intent_learning_records
                SET feedback_status=%s, suggested_intent=%s, feedback_reason=%s,
                    badcase_id=%s, updated_at=%s
                WHERE prediction_id=%s
                """,
                (
                    IntentFeedbackStatus.PENDING.value, suggested, safe_reason,
                    case.badcase_id, now, prediction_id,
                ),
            )
            conn.execute(
                """
                UPDATE dialogpilot_platform.bad_cases
                SET prediction_id=%s, predicted_intent=%s, suggested_intent=%s,
                    classifier_fingerprint=%s, updated_at=%s
                WHERE badcase_id=%s
                """,
                (
                    prediction_id, prediction["predicted_intent"], suggested,
                    prediction["classifier_fingerprint"], now, case.badcase_id,
                ),
            )
            self._insert_intent_learning_event(
                conn, prediction_id, "feedback_submitted", prediction["user_ref"],
                {"suggested_intent": suggested, "badcase_id": case.badcase_id}, now,
            )
            learning_row = conn.execute(
                "SELECT * FROM dialogpilot_platform.intent_learning_records WHERE prediction_id=%s",
                (prediction_id,),
            ).fetchone()
            case_row = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s", (case.badcase_id,),
            ).fetchone()
        return (
            self._row_to_intent_learning(learning_row),
            self._row_to_badcase(case_row),
            created,
        )

    def review_intent_feedback(
        self,
        prediction_id: str,
        *,
        decision: IntentFeedbackStatus,
        actor: str,
        approved_intent: str = "",
        dataset_version: str = "",
        note: str = "",
    ) -> Tuple[IntentLearningRecord, BadCase]:
        """人工批准或拒绝候选标签；批准只形成可评测标签，不直接激活 Bundle。"""
        prediction_id = self._required(prediction_id, "prediction_id")[:64]
        decision = IntentFeedbackStatus(decision)
        if decision not in {IntentFeedbackStatus.APPROVED, IntentFeedbackStatus.REJECTED}:
            raise BadCaseContractError("review decision must be approved or rejected")
        actor = self._required(actor, "actor")[:200]
        approved = ""
        dataset = ""
        if decision is IntentFeedbackStatus.APPROVED:
            approved = self._intent_name(approved_intent, "approved_intent")
            dataset = self._slug(self._required(dataset_version, "dataset_version"), 128)
        safe_note = self.sanitize_text(note, 1000)

        with self._connect() as conn:
            prediction = conn.execute(
                "SELECT * FROM dialogpilot_platform.intent_learning_records WHERE prediction_id=%s FOR UPDATE",
                (prediction_id,),
            ).fetchone()
            if prediction is None:
                raise BadCaseNotFoundError("intent prediction not found")
            current = IntentFeedbackStatus(prediction["feedback_status"])
            if current is decision:
                if decision is IntentFeedbackStatus.APPROVED and (
                    prediction["approved_intent"] != approved
                    or prediction["dataset_version"] != dataset
                ):
                    raise BadCaseContractError("approved annotation is immutable")
                case_row = conn.execute(
                    "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s FOR UPDATE",
                    (prediction["badcase_id"],),
                ).fetchone()
                return self._row_to_intent_learning(prediction), self._row_to_badcase(case_row)
            if current is not IntentFeedbackStatus.PENDING:
                raise BadCaseContractError("only pending intent feedback can be reviewed")
            if (
                decision is IntentFeedbackStatus.APPROVED
                and approved == prediction["predicted_intent"]
            ):
                raise BadCaseContractError(
                    "an approved wrong-route label must differ from the original prediction; "
                    "reject the feedback when the prediction was correct"
                )
            case_row = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s FOR UPDATE",
                (prediction["badcase_id"],),
            ).fetchone()
            if case_row is None or BadCaseStage(case_row["stage"]) is not BadCaseStage.INTENT:
                raise BadCaseContractError("intent feedback must reference an intent bad case")

            now = self._now()
            # 被拒绝的反馈仍保留 reviewer/note/event，但不能产生一个看似已批准的
            # annotation_id。只有 APPROVED 才拥有可供候选生成消费的标签事实。
            annotation_id = (
                uuid.uuid4().hex
                if decision is IntentFeedbackStatus.APPROVED else ""
            )
            conn.execute(
                """
                UPDATE dialogpilot_platform.intent_learning_records
                SET feedback_status=%s, approved_intent=%s, annotation_id=%s, reviewer=%s,
                    dataset_version=%s, review_note=%s, updated_at=%s
                WHERE prediction_id=%s
                """,
                (
                    decision.value, approved, annotation_id, actor, dataset,
                    safe_note, now, prediction_id,
                ),
            )
            badcase_status = BadCaseStatus(case_row["status"])
            next_status = (
                BadCaseStatus.TRIAGED
                if decision is IntentFeedbackStatus.APPROVED
                and badcase_status is BadCaseStatus.CANDIDATE
                else badcase_status
            )
            expected = (
                {"intent": approved, "annotation_id": annotation_id}
                if decision is IntentFeedbackStatus.APPROVED
                else self._load_json(case_row["expected_json"])
            )
            conn.execute(
                """
                UPDATE dialogpilot_platform.bad_cases
                SET status=%s, approved_intent=%s, annotation_id=%s, expected_json=%s, updated_at=%s
                WHERE badcase_id=%s
                """,
                (
                    next_status.value, approved, annotation_id,
                    self._json(expected), now, case_row["badcase_id"],
                ),
            )
            self._insert_event(
                conn, case_row["badcase_id"], badcase_status, next_status, actor,
                self.sanitize_text(
                    f"intent feedback {decision.value}; annotation={annotation_id}; {safe_note}",
                    1000,
                ),
                now,
            )
            self._insert_intent_learning_event(
                conn, prediction_id, f"feedback_{decision.value}", actor,
                {
                    "annotation_id": annotation_id,
                    "approved_intent": approved,
                    "dataset_version": dataset,
                },
                now,
            )
            learning_row = conn.execute(
                "SELECT * FROM dialogpilot_platform.intent_learning_records WHERE prediction_id=%s",
                (prediction_id,),
            ).fetchone()
            updated_case = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s FOR UPDATE",
                (case_row["badcase_id"],),
            ).fetchone()
        return self._row_to_intent_learning(learning_row), self._row_to_badcase(updated_case)

    def get_intent_prediction(
        self,
        prediction_id: str,
        *,
        user_id: str = "",
    ) -> IntentLearningRecord:
        """读取预测；传入 user_id 时执行不泄露存在性的所有权检查。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dialogpilot_platform.intent_learning_records WHERE prediction_id=%s",
                (str(prediction_id)[:64],),
            ).fetchone()
        if row is None or (user_id and row["user_ref"] != self._user_ref(user_id)):
            raise BadCaseNotFoundError("intent prediction not found")
        return self._row_to_intent_learning(row)

    def list_intent_learning(
        self,
        *,
        status: Optional[IntentFeedbackStatus] = None,
        limit: int = 50,
    ) -> List[IntentLearningRecord]:
        """管理员读取预测/反馈队列；普通聊天接口不暴露该投影。"""
        params: List[Any] = []
        where = ""
        if status is not None:
            where = " WHERE feedback_status=%s"
            params.append(IntentFeedbackStatus(status).value)
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM dialogpilot_platform.intent_learning_records{where} ORDER BY updated_at DESC LIMIT %s",
                tuple(params),
            ).fetchall()
        return [self._row_to_intent_learning(row) for row in rows]

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
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s FOR UPDATE", (badcase_id,)
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
                UPDATE dialogpilot_platform.bad_cases SET status=%s, root_cause=%s, owner_module=%s, eval_layer=%s,
                    expected_json=%s, reproduction_json=%s, fixed_by_commit=%s, updated_at=%s
                WHERE badcase_id=%s
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
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s", (badcase_id,)
            ).fetchone()
            return self._row_to_badcase(updated)

    def link_regression(self, badcase_id: str, case_id: str, *, actor: str) -> BadCase:
        """把导出的 regression ID 回写为非权威引用；不改变生命周期状态。"""
        case_id = self._slug(self._required(case_id, "case_id"), 200)
        actor = self._required(actor, "actor")[:200]
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s FOR UPDATE", (badcase_id,)
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
                "UPDATE dialogpilot_platform.bad_cases SET linked_case_id=%s, updated_at=%s WHERE badcase_id=%s",
                (case_id, now, badcase_id),
            )
            self._insert_event(
                conn, badcase_id, BadCaseStatus(row["status"]), BadCaseStatus(row["status"]),
                actor, f"linked regression case {case_id}", now,
            )
            updated = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s", (badcase_id,)
            ).fetchone()
            return self._row_to_badcase(updated)

    def get(self, badcase_id: str) -> BadCase:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s", (badcase_id,)
            ).fetchone()
        if row is None:
            raise BadCaseNotFoundError(f"badcase not found: {badcase_id}")
        return self._row_to_badcase(row)

    def get_view(self, badcase_id: str) -> Dict[str, Any]:
        case = self.get(badcase_id)
        with self._connect() as conn:
            events = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_case_events WHERE badcase_id=%s ORDER BY event_id",
                (badcase_id,),
            ).fetchall()
            occurrences = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_case_occurrences WHERE badcase_id=%s ORDER BY occurrence_id",
                (badcase_id,),
            ).fetchall()
            intent_record = None
            intent_events = []
            if case.prediction_id:
                intent_record = conn.execute(
                    "SELECT * FROM dialogpilot_platform.intent_learning_records WHERE prediction_id=%s",
                    (case.prediction_id,),
                ).fetchone()
                intent_events = conn.execute(
                    "SELECT * FROM dialogpilot_platform.intent_learning_events WHERE prediction_id=%s ORDER BY event_id",
                    (case.prediction_id,),
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
        view["intent_learning"] = (
            self._row_to_intent_learning(intent_record).to_dict()
            if intent_record is not None else None
        )
        view["intent_learning_events"] = [
            {
                **dict(event),
                "payload": self._load_json(event["payload_json"]),
            }
            for event in intent_events
        ]
        for event in view["intent_learning_events"]:
            event.pop("payload_json", None)
        return view

    def list(
        self,
        *,
        status: Optional[BadCaseStatus] = None,
        stage: Optional[BadCaseStage] = None,
        severity: Optional[BadCaseSeverity] = None,
        semantic_group_id: Optional[str] = None,
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
                clauses.append(f"{column}=%s")
                params.append(enum_type(value).value)
        if semantic_group_id is not None:
            clauses.append("semantic_group_id=%s")
            params.append(self._slug(semantic_group_id, 160))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM dialogpilot_platform.bad_cases{where} ORDER BY last_seen_at DESC LIMIT %s",
                tuple(params),
            ).fetchall()
        return [self._row_to_badcase(row) for row in rows]

    def stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) AS count FROM dialogpilot_platform.bad_cases").fetchone()["count"])
            by_status = {row["status"]: int(row["count"]) for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM dialogpilot_platform.bad_cases GROUP BY status"
            ).fetchall()}
            recurrences = int(conn.execute(
                "SELECT COALESCE(SUM(occurrence_count - 1), 0) AS count FROM dialogpilot_platform.bad_cases"
            ).fetchone()["count"])
            intent_predictions = int(conn.execute(
                "SELECT COUNT(*) AS count FROM dialogpilot_platform.intent_learning_records"
            ).fetchone()["count"])
            intent_feedback = {row["feedback_status"]: int(row["count"]) for row in conn.execute(
                "SELECT feedback_status, COUNT(*) AS count FROM dialogpilot_platform.intent_learning_records GROUP BY feedback_status"
            ).fetchall()}
        return {
            "total": total,
            "by_status": by_status,
            "recurrences": recurrences,
            "intent_predictions": intent_predictions,
            "intent_feedback": intent_feedback,
        }

    @classmethod
    def sanitize_text(cls, value: Any, max_chars: int = 10000) -> str:
        text = unicodedata.normalize("NFKC", str(value or ""))
        for pattern, replacement in cls._SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        return text[:max(0, int(max_chars))]

    def _observe_with_connection(
        self,
        conn: Cursor,
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
        """在调用方事务中写 observation，供普通捕获和意图反馈共用。"""
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
        user_ref = self._user_ref(user_id)
        safe_evidence = self._sanitize_value(dict(evidence or {}))
        safe_versions = self._sanitize_value(dict(versions or {}))
        group_id = (
            self._slug(semantic_group_id, 160)
            if semantic_group_id else f"badcase-{fingerprint[:16]}"
        )

        # Serialize first insertion as well as recurrences; row locks alone cannot
        # protect a fingerprint that has not been inserted yet.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("badcase:" + fingerprint,),
        )
        row = conn.execute(
            "SELECT * FROM dialogpilot_platform.bad_cases WHERE fingerprint = %s FOR UPDATE", (fingerprint,)
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
                UPDATE dialogpilot_platform.bad_cases SET occurrence_count=occurrence_count+1,
                    status=%s, severity=%s, trace_id=%s, request_id=%s, published_response=%s,
                    evidence_json=%s, versions_json=%s, last_seen_at=%s, updated_at=%s
                WHERE badcase_id=%s
                """,
                (
                    next_status.value, next_severity.value, str(trace_id)[:128],
                    str(request_id)[:200], safe_response, self._json(safe_evidence),
                    self._json(safe_versions), now, now, row["badcase_id"],
                ),
            )
            self._insert_occurrence(
                conn, row["badcase_id"], source, severity, str(trace_id)[:128],
                str(request_id)[:200], safe_input, safe_response,
                safe_evidence, safe_versions, now,
            )
            self._insert_event(
                conn, row["badcase_id"], current, next_status, "system",
                f"recurrence observed from {source}" if current is BadCaseStatus.CLOSED
                else f"duplicate observation from {source}",
                now,
            )
            updated = conn.execute(
                "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s", (row["badcase_id"],)
            ).fetchone()
            return self._row_to_badcase(updated), False

        conn.execute(
            """
            INSERT INTO dialogpilot_platform.bad_cases (
                badcase_id, fingerprint, semantic_group_id, source, stage, severity,
                status, symptom_code, trace_id, request_id, user_ref,
                sanitized_input, published_response, evidence_json, versions_json,
                eval_layer, expected_json, reproduction_json, root_cause,
                owner_module, linked_case_id, fixed_by_commit, occurrence_count,
                created_at, first_seen_at, last_seen_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', '{}', '{}',
                      '', '', '', '', 1, %s, %s, %s, %s)
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
            "SELECT * FROM dialogpilot_platform.bad_cases WHERE badcase_id=%s", (badcase_id,)
        ).fetchone()
        return self._row_to_badcase(row), True

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


    @staticmethod
    def _insert_event(
        conn: Cursor,
        badcase_id: str,
        current: Optional[BadCaseStatus],
        target: BadCaseStatus,
        actor: str,
        note: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO dialogpilot_platform.bad_case_events (
                badcase_id, from_status, to_status, actor, note, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                badcase_id, current.value if current else None, target.value,
                actor, note, created_at,
            ),
        )

    @classmethod
    def _insert_intent_learning_event(
        cls,
        conn: Cursor,
        prediction_id: str,
        event_type: str,
        actor: str,
        payload: Mapping[str, Any],
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO dialogpilot_platform.intent_learning_events (
                prediction_id, event_type, actor, payload_json, created_at
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                prediction_id, str(event_type)[:80], str(actor)[:200],
                cls._json(dict(payload)), created_at,
            ),
        )

    @classmethod
    def _insert_occurrence(
        cls,
        conn: Cursor,
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
            INSERT INTO dialogpilot_platform.bad_case_occurrences (
                badcase_id, source, severity, trace_id, request_id,
                sanitized_input, published_response, evidence_json,
                versions_json, observed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                badcase_id, source, severity.value, trace_id, request_id,
                sanitized_input, published_response, cls._json(evidence),
                cls._json(versions), observed_at,
            ),
        )

    @classmethod
    def _row_to_badcase(cls, row: Mapping[str, Any]) -> BadCase:
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
            prediction_id=row["prediction_id"], predicted_intent=row["predicted_intent"],
            suggested_intent=row["suggested_intent"], approved_intent=row["approved_intent"],
            annotation_id=row["annotation_id"],
            classifier_fingerprint=row["classifier_fingerprint"],
        )

    @classmethod
    def _row_to_intent_learning(cls, row: Mapping[str, Any]) -> IntentLearningRecord:
        if row is None:
            raise BadCaseNotFoundError("intent prediction not found")
        scores = cls._load_json(row["source_scores_json"])
        return IntentLearningRecord(
            prediction_id=row["prediction_id"], request_id=row["request_id"],
            trace_id=row["trace_id"], conv_id=row["conv_id"], user_ref=row["user_ref"],
            input_fingerprint=row["input_fingerprint"],
            sanitized_input=row["sanitized_input"], predicted_intent=row["predicted_intent"],
            confidence=float(row["confidence"]),
            source_scores={str(key): float(value) for key, value in scores.items()},
            classifier_fingerprint=row["classifier_fingerprint"],
            bundle_version=row["bundle_version"],
            feedback_status=IntentFeedbackStatus(row["feedback_status"]),
            suggested_intent=row["suggested_intent"], feedback_reason=row["feedback_reason"],
            badcase_id=row["badcase_id"], approved_intent=row["approved_intent"],
            annotation_id=row["annotation_id"], reviewer=row["reviewer"],
            dataset_version=row["dataset_version"], review_note=row["review_note"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _required(value: Any, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise BadCaseContractError(f"{field} is required")
        return text

    def _user_ref(self, user_id: str) -> str:
        if not self._identity_salt:
            raise BadCaseContractError("identity_salt is required to observe user data")
        return hmac.new(
            self._identity_salt,
            str(user_id or "anonymous").encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:20]

    @staticmethod
    def _sha256(value: Any, field: str) -> str:
        digest = str(value or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BadCaseContractError(f"{field} must be a SHA-256 digest")
        return digest

    @staticmethod
    def _intent_name(value: Any, field: str) -> str:
        name = str(getattr(value, "value", value) or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise BadCaseContractError(f"{field} must be a supported intent name")
        return name

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
