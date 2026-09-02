"""Agent-owned, replayable intent/domain/instance routing policies."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Sequence

from agents.orchestration_contracts import AgentType
from core.intent_recognizer import IntentCategory, UrgencyLevel


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(
        _plain(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(getattr(key, "value", key)): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass(frozen=True)
class IntentFusionPolicy:
    version: str
    weights_by_mode: Mapping[str, Mapping[str, float]]
    accept_threshold: float

    def __post_init__(self) -> None:
        expected = {
            "ngram": {"llm", "embedding", "pattern"},
            "disabled": {"llm", "pattern"},
        }
        if set(self.weights_by_mode) != set(expected):
            raise ValueError("intent policy must close both V1 modes")
        for mode, sources in expected.items():
            weights = self.weights_by_mode[mode]
            if set(weights) != sources or abs(sum(weights.values()) - 1.0) > 1e-9:
                raise ValueError(f"invalid intent fusion weights for {mode}")
        if not 0 < self.accept_threshold <= 1:
            raise ValueError("intent accept threshold is invalid")

    @property
    def fingerprint(self) -> str:
        return _fingerprint({
            "version": self.version,
            "weights_by_mode": self.weights_by_mode,
            "accept_threshold": self.accept_threshold,
        })


@dataclass(frozen=True)
class DomainDecision:
    ordered_owners: tuple[AgentType, ...]
    scores: Mapping[AgentType, float]
    components: Mapping[AgentType, Mapping[str, float]]
    hard_rule_reason: str | None
    policy_version: str
    policy_fingerprint: str
    input_fingerprint: str
    selected_owners: tuple[AgentType, ...] = ()


@dataclass(frozen=True)
class DomainRoutingPolicy:
    version: str
    general_prior: float = 0.10
    supporting_threshold: float = 0.45
    clarification_threshold: float = 0.50

    @property
    def fingerprint(self) -> str:
        return _fingerprint(asdict(self))

    def decide(
        self,
        *,
        message: str,
        intent: IntentCategory | None,
        urgency: UrgencyLevel | None,
        entities: Mapping[str, Sequence[str]],
        available_owners: Sequence[AgentType],
    ) -> DomainDecision:
        input_payload = {
            "message": message,
            "intent": intent.value if intent else None,
            "urgency": urgency.name if urgency else None,
            "entities": {key: list(value) for key, value in sorted(entities.items())},
            "available_owners": sorted(item.value for item in available_owners),
        }
        input_fingerprint = _fingerprint(input_payload)
        if urgency is UrgencyLevel.CRITICAL:
            return self._hard(AgentType.ESCALATION, "CRITICAL_URGENCY", input_fingerprint)
        if intent in {IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF}:
            return self._hard(AgentType.ESCALATION, "EXPLICIT_HUMAN_HANDOFF", input_fingerprint)

        components: dict[AgentType, dict[str, float]] = {
            AgentType.GENERAL: {"prior": self.general_prior},
            AgentType.TECHNICAL: {"prior": 0.0},
            AgentType.BILLING: {"prior": 0.0},
            AgentType.ACCOUNT_SECURITY: {"prior": 0.0},
        }
        intent_domains = {
            AgentType.GENERAL: {
                IntentCategory.QUERY, IntentCategory.ORDER_STATUS,
                IntentCategory.LOGISTICS, IntentCategory.REQUEST,
                IntentCategory.COMPLAINT, IntentCategory.GREETING,
                IntentCategory.FEEDBACK, IntentCategory.OTHER,
                IntentCategory.ACCOUNT,
            },
            AgentType.TECHNICAL: {
                IntentCategory.TECHNICAL, IntentCategory.TECHNICAL_LOGIN,
                IntentCategory.TECHNICAL_CRASH,
            },
            AgentType.BILLING: {
                IntentCategory.BILLING, IntentCategory.REFUND,
                IntentCategory.INVOICE, IntentCategory.PAYMENT_ISSUE,
            },
            AgentType.ACCOUNT_SECURITY: {IntentCategory.ACCOUNT_SECURITY},
        }
        intent_values = {
            AgentType.GENERAL: 0.55,
            AgentType.TECHNICAL: 0.75,
            AgentType.BILLING: 0.75,
            AgentType.ACCOUNT_SECURITY: 0.85,
        }
        for owner, intents in intent_domains.items():
            if intent in intents:
                components[owner]["intent"] = intent_values[owner]

        normalized = message.lower()
        keyword_rules = {
            AgentType.TECHNICAL: (
                ("崩溃", "报错", "error", "crash", "无法登录", "登录失败", "500", "401", "验证码"),
                0.45, 0.10, 0.65,
            ),
            AgentType.BILLING: (
                ("退款", "退货", "扣款", "扣费", "扣了", "重复扣", "多扣", "发票", "账单", "支付", "订阅", "refund", "invoice"),
                0.45, 0.10, 0.65,
            ),
            AgentType.ACCOUNT_SECURITY: (
                ("账号被盗", "账户被盗", "异常登录", "陌生设备", "密码泄露", "账号安全", "账户安全", "身份验证", "冻结账号", "盗号", "unauthorized login", "hacked"),
                0.55, 0.10, 0.75,
            ),
        }
        for owner, (keywords, first, additional, cap) in keyword_rules.items():
            hits = _affirmed_keyword_hits(normalized, keywords)
            if hits:
                components[owner]["keywords"] = min(
                    cap, first + (hits - 1) * additional,
                )
        general_hits = _affirmed_keyword_hits(
            normalized, ("订单", "物流", "快递", "配送", "会员", "积分", "咨询", "帮助"),
        )
        if general_hits:
            components[AgentType.GENERAL]["keywords"] = min(0.35, general_hits * 0.12)
        for entity, owner, value in (
            ("error_code", AgentType.TECHNICAL, 0.20),
            ("amount", AgentType.BILLING, 0.15),
            ("order_id", AgentType.GENERAL, 0.10),
        ):
            if entities.get(entity):
                components[owner][f"entity:{entity}"] = value
        available = set(available_owners)
        scores = {
            owner: round(sum(values.values()), 3)
            for owner, values in components.items()
            if owner is AgentType.GENERAL or owner in available
        }
        ordered = tuple(
            owner for owner, _ in sorted(
                scores.items(), key=lambda item: (-item[1], item[0].value),
            )
        )
        selected = (
            (ordered[0],) + tuple(
                owner for owner in ordered[1:]
                if owner is not AgentType.GENERAL
                and scores[owner] >= self.supporting_threshold
            )
            if ordered else ()
        )
        return DomainDecision(
            ordered, MappingProxyType(scores),
            MappingProxyType({
                owner: MappingProxyType(dict(values))
                for owner, values in components.items() if owner in scores
            }),
            None, self.version, self.fingerprint, input_fingerprint, selected,
        )

    def _hard(
        self, owner: AgentType, reason: str, input_fingerprint: str,
    ) -> DomainDecision:
        return DomainDecision(
            (owner,), MappingProxyType({owner: 1.0}),
            MappingProxyType({owner: MappingProxyType({"hard_rule": 1.0})}),
            reason, self.version, self.fingerprint, input_fingerprint, (owner,),
        )


class InstanceSelectionStatus(str, Enum):
    SELECTED = "SELECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class AgentHealthSnapshot:
    instance_id: str
    total: int
    success: int
    total_ms: float
    quality_samples: int
    quality_ewma: float
    monitor_penalty: float


@dataclass(frozen=True)
class InstanceSelectionDecision:
    status: InstanceSelectionStatus
    selected_instance_id: str | None
    scores: Mapping[str, float]
    snapshot_fingerprint: str
    reason_code: str
    policy_version: str
    policy_fingerprint: str


@dataclass(frozen=True)
class InstanceSelectionPolicy:
    version: str
    success_weight: float = 0.35
    quality_weight: float = 0.45
    latency_weight: float = 0.20
    quality_ewma_alpha: float = 0.25
    quality_prior: float = 0.50
    full_confidence_samples: int = 10
    latency_normalizer_ms: float = 1000.0
    monitor_success_threshold: float = 0.90
    monitor_success_slope: float = 2.0
    monitor_success_cap: float = 0.50
    monitor_latency_threshold_ms: float = 3000.0
    monitor_latency_slope_ms: float = 10000.0
    monitor_latency_cap: float = 0.40
    monitor_total_cap: float = 0.90

    @property
    def fingerprint(self) -> str:
        return _fingerprint(asdict(self))

    def select(
        self, snapshots: Sequence[AgentHealthSnapshot],
    ) -> InstanceSelectionDecision:
        ordered = tuple(sorted(snapshots, key=lambda item: item.instance_id))
        snapshot_fingerprint = _fingerprint([asdict(item) for item in ordered])
        if not ordered:
            return InstanceSelectionDecision(
                InstanceSelectionStatus.UNAVAILABLE, None, MappingProxyType({}),
                snapshot_fingerprint, "OWNER_POOL_EMPTY", self.version,
                self.fingerprint,
            )
        scores = MappingProxyType({
            item.instance_id: round(self._score(item), 12) for item in ordered
        })
        if len(ordered) == 1:
            return InstanceSelectionDecision(
                InstanceSelectionStatus.NOT_APPLICABLE, ordered[0].instance_id,
                scores, snapshot_fingerprint, "OWNER_POOL_SINGLETON",
                self.version, self.fingerprint,
            )
        selected = min(
            ordered,
            key=lambda item: (-scores[item.instance_id], item.instance_id),
        )
        return InstanceSelectionDecision(
            InstanceSelectionStatus.SELECTED, selected.instance_id, scores,
            snapshot_fingerprint, "HIGHEST_FROZEN_HEALTH_SCORE",
            self.version, self.fingerprint,
        )

    def _score(self, item: AgentHealthSnapshot) -> float:
        success_rate = item.success / item.total if item.total else 1.0
        average_ms = item.total_ms / item.total if item.total else 0.0
        confidence = min(1.0, item.quality_samples / self.full_confidence_samples)
        quality = self.quality_prior * (1.0 - confidence) + item.quality_ewma * confidence
        latency = 1.0 / (1.0 + average_ms / self.latency_normalizer_ms)
        base = (
            success_rate * self.success_weight
            + quality * self.quality_weight
            + latency * self.latency_weight
        )
        return base * max(0.0, 1.0 - min(item.monitor_penalty, self.monitor_total_cap))


@dataclass(frozen=True)
class AgentRoutingPolicyRegistry:
    version: str
    intent_fusion: IntentFusionPolicy
    domain_routing: DomainRoutingPolicy
    instance_selection: InstanceSelectionPolicy

    @classmethod
    def v1(cls) -> "AgentRoutingPolicyRegistry":
        return cls(
            version="agent-routing-policy-registry-v1",
            intent_fusion=IntentFusionPolicy(
                version="intent-fusion-v1",
                weights_by_mode=MappingProxyType({
                    "ngram": MappingProxyType({
                        "llm": 0.70, "embedding": 0.20, "pattern": 0.10,
                    }),
                    "disabled": MappingProxyType({
                        "llm": 0.85, "pattern": 0.15,
                    }),
                }),
                accept_threshold=0.50,
            ),
            domain_routing=DomainRoutingPolicy("domain-routing-v1"),
            instance_selection=InstanceSelectionPolicy("instance-selection-v1"),
        )

    @property
    def fingerprint(self) -> str:
        return _fingerprint({
            "version": self.version,
            "intent": self.intent_fusion.fingerprint,
            "domain": self.domain_routing.fingerprint,
            "instance": self.instance_selection.fingerprint,
        })

    def pinned_config_ref(self) -> Mapping[str, str]:
        return MappingProxyType({
            "registry_version": self.version,
            "registry_fingerprint": self.fingerprint,
            "intent_policy_version": self.intent_fusion.version,
            "intent_policy_fingerprint": self.intent_fusion.fingerprint,
            "domain_policy_version": self.domain_routing.version,
            "domain_policy_fingerprint": self.domain_routing.fingerprint,
            "instance_policy_version": self.instance_selection.version,
            "instance_policy_fingerprint": self.instance_selection.fingerprint,
        })


@dataclass
class RoutingPolicyTrace:
    pinned_config_ref: Mapping[str, str]
    intent_classifier_fingerprint: str = ""
    intent_input_fingerprint: str = ""
    intent_source_scores: Mapping[str, float] | None = None
    domain_decisions: list[DomainDecision] | None = None
    instance_decisions: list[InstanceSelectionDecision] | None = None

    def __post_init__(self) -> None:
        self.intent_source_scores = MappingProxyType(dict(
            self.intent_source_scores or {}
        ))
        self.domain_decisions = list(self.domain_decisions or [])
        self.instance_decisions = list(self.instance_decisions or [])

    def to_dict(self) -> dict[str, object]:
        return {
            "pinned_config_ref": dict(self.pinned_config_ref),
            "intent": {
                "status": "INVOKED" if self.intent_classifier_fingerprint else "SKIPPED",
                "classifier_fingerprint": self.intent_classifier_fingerprint,
                "input_fingerprint": self.intent_input_fingerprint,
                "source_scores": dict(self.intent_source_scores or {}),
            },
            "domain": [
                {
                    "status": "INVOKED",
                    "policy_version": item.policy_version,
                    "policy_fingerprint": item.policy_fingerprint,
                    "input_fingerprint": item.input_fingerprint,
                    "hard_rule_reason": item.hard_rule_reason,
                    "ordered_owners": [owner.value for owner in item.ordered_owners],
                    "selected_owners": [owner.value for owner in item.selected_owners],
                    "scores": {owner.value: score for owner, score in item.scores.items()},
                    "components": {
                        owner.value: dict(values)
                        for owner, values in item.components.items()
                    },
                }
                for item in (self.domain_decisions or [])
            ],
            "instance": [
                {
                    "status": item.status.value,
                    "selected_instance_id": item.selected_instance_id,
                    "scores": dict(item.scores),
                    "snapshot_fingerprint": item.snapshot_fingerprint,
                    "reason_code": item.reason_code,
                    "policy_version": item.policy_version,
                    "policy_fingerprint": item.policy_fingerprint,
                }
                for item in (self.instance_decisions or [])
            ],
        }


def _affirmed_keyword_hits(message: str, keywords: Sequence[str]) -> int:
    negators = ("不是", "并非", "没有", "不涉及", "无关", "不要", "别")
    hits = 0
    for keyword in keywords:
        start = 0
        while True:
            index = message.find(keyword, start)
            if index < 0:
                break
            prefix = message[max(0, index - 8):index]
            if not any(negator in prefix for negator in negators):
                hits += 1
                break
            start = index + len(keyword)
    return hits
