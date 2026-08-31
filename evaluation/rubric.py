"""Case 级 Rubric 合同：确定性硬约束与语义评分标准分离。"""
from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata
from typing import Any, Dict, Mapping, Tuple


class RubricValidationError(ValueError):
    """Rubric 结构或阈值超出支持范围。"""


@dataclass(frozen=True)
class RubricEvaluation:
    """确定性 Rubric 的逐项证据；硬失败不能被模型平均分抵消。"""

    hard_pass: bool
    checks: Dict[str, bool]
    violations: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def score(self) -> float:
        return sum(self.checks.values()) / len(self.checks) if self.checks else 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hard_pass": self.hard_pass,
            "score": round(self.score, 4),
            "checks": dict(self.checks),
            "violations": list(self.violations),
        }


@dataclass(frozen=True)
class CaseRubric:
    """一条对话 Case 的硬约束、语义标准和逐维最低分。"""

    rubric_id: str = "default"
    must_include_all: Tuple[str, ...] = field(default_factory=tuple)
    must_include_any: Tuple[str, ...] = field(default_factory=tuple)
    forbidden_phrases: Tuple[str, ...] = field(default_factory=tuple)
    semantic_criteria: Tuple[str, ...] = field(default_factory=tuple)
    min_dimension_scores: Dict[str, float] = field(default_factory=dict)
    max_tool_calls: int | None = None

    _DIMENSIONS = frozenset({"relevance", "accuracy", "completeness", "helpfulness"})

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "CaseRubric":
        data = dict(raw or {})
        minimums = {
            str(key): float(value)
            for key, value in dict(data.get("min_dimension_scores") or {}).items()
        }
        unknown = set(minimums) - cls._DIMENSIONS
        if unknown:
            raise RubricValidationError(f"unsupported rubric dimensions: {sorted(unknown)}")
        if any(value < 0.0 or value > 1.0 for value in minimums.values()):
            raise RubricValidationError("rubric dimension thresholds must be in [0, 1]")
        max_tool_calls = data.get("max_tool_calls")
        if max_tool_calls is not None and int(max_tool_calls) < 0:
            raise RubricValidationError("max_tool_calls must be non-negative")
        return cls(
            rubric_id=str(data.get("id") or "default")[:160],
            must_include_all=cls._strings(data.get("must_include_all")),
            must_include_any=cls._strings(data.get("must_include_any")),
            forbidden_phrases=cls._strings(data.get("forbidden_phrases")),
            semantic_criteria=cls._strings(data.get("semantic_criteria")),
            min_dimension_scores=minimums,
            max_tool_calls=int(max_tool_calls) if max_tool_calls is not None else None,
        )

    @staticmethod
    def _strings(value: Any) -> Tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise RubricValidationError("rubric phrase fields must be arrays")
        return tuple(str(item).strip() for item in value if str(item).strip())

    @staticmethod
    def _normalize(value: Any) -> str:
        return unicodedata.normalize("NFKC", str(value or "")).casefold()

    def evaluate(self, response: str, *, tool_call_count: int = 0) -> RubricEvaluation:
        normalized = self._normalize(response)
        checks: Dict[str, bool] = {}
        violations = []
        for index, phrase in enumerate(self.must_include_all):
            passed = self._normalize(phrase) in normalized
            checks[f"must_include_all[{index}]"] = passed
            if not passed:
                violations.append(f"missing required phrase: {phrase}")
        if self.must_include_any:
            passed = any(self._normalize(phrase) in normalized for phrase in self.must_include_any)
            checks["must_include_any"] = passed
            if not passed:
                violations.append("none of the required alternative phrases appeared")
        for index, phrase in enumerate(self.forbidden_phrases):
            passed = self._normalize(phrase) not in normalized
            checks[f"forbidden_phrases[{index}]"] = passed
            if not passed:
                violations.append(f"forbidden phrase appeared: {phrase}")
        if self.max_tool_calls is not None:
            passed = int(tool_call_count) <= self.max_tool_calls
            checks["max_tool_calls"] = passed
            if not passed:
                violations.append(
                    f"tool calls {int(tool_call_count)} exceeded maximum {self.max_tool_calls}"
                )
        return RubricEvaluation(
            hard_pass=all(checks.values()) if checks else True,
            checks=checks,
            violations=tuple(violations),
        )

    def semantic_prompt(self) -> str:
        """只把需语义判断的标准交给 Judge，硬约束仍由代码评分。"""
        lines = []
        if self.semantic_criteria:
            lines.append("本 Case 的额外语义 Rubric：")
            lines.extend(f"- {criterion}" for criterion in self.semantic_criteria)
        if self.min_dimension_scores:
            rendered = ", ".join(
                f"{key}>={value:.2f}" for key, value in sorted(self.min_dimension_scores.items())
            )
            lines.append(f"逐维最低分：{rendered}")
        return "\n".join(lines)

