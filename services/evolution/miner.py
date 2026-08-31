"""把已脱敏 Bad Case 聚成可归因、可复现的问题组。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

from services.badcase_registry import BadCase, BadCaseStatus


@dataclass(frozen=True)
class BadCaseCluster:
    group_id: str
    cases: Tuple[BadCase, ...]

    @property
    def stages(self) -> Tuple[str, ...]:
        return tuple(sorted({case.stage.value for case in self.cases}))

    @property
    def symptoms(self) -> Tuple[str, ...]:
        return tuple(sorted({case.symptom_code for case in self.cases}))

    @property
    def occurrence_count(self) -> int:
        return sum(case.occurrence_count for case in self.cases)

    def reflection_summary(self) -> dict:
        """只给候选生成器必要的脱敏诊断，不携带原始请求/响应。"""
        approved_intent_examples = [
            {
                "message": case.sanitized_input,
                "intent": case.approved_intent,
                "annotation_id": case.annotation_id,
                "classifier_fingerprint": case.classifier_fingerprint,
            }
            for case in self.cases
            if case.stage.value == "intent"
            and case.approved_intent
            and case.annotation_id
        ][:20]
        return {
            "group_id": self.group_id,
            "stages": list(self.stages),
            "symptoms": list(self.symptoms),
            "occurrence_count": self.occurrence_count,
            "root_causes": sorted({case.root_cause for case in self.cases if case.root_cause}),
            "owner_modules": sorted({case.owner_module for case in self.cases if case.owner_module}),
            "expected_behaviors": [
                case.expected_behavior for case in self.cases if case.expected_behavior
            ][:20],
            "approved_intent_examples": approved_intent_examples,
        }


class BadCaseMiner:
    """按显式 semantic_group_id 聚类；不让 Embedding 临时重定义问题身份。"""

    _EXCLUDED = {
        BadCaseStatus.DUPLICATE,
        BadCaseStatus.NOT_A_BUG,
        BadCaseStatus.PRIVACY_REJECTED,
        BadCaseStatus.CLOSED,
    }

    def cluster(self, cases: Iterable[BadCase]) -> Tuple[BadCaseCluster, ...]:
        grouped = {}
        for case in cases:
            if case.status in self._EXCLUDED:
                continue
            grouped.setdefault(case.semantic_group_id, []).append(case)
        return tuple(
            BadCaseCluster(
                group_id=group_id,
                cases=tuple(sorted(rows, key=lambda item: (item.first_seen_at, item.badcase_id))),
            )
            for group_id, rows in sorted(grouped.items())
        )
