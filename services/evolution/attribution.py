"""Bad Case 到权威可进化配置面的责任归因。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from services.badcase_registry import BadCaseStage
from .miner import BadCaseCluster


class EvolutionSurface(str, Enum):
    PROMPTS = "prompts"
    FEW_SHOTS = "few_shots"
    ROUTING = "routing_policy"
    RETRIEVAL = "retrieval_policy"
    TOOL_DESCRIPTIONS = "tool_descriptions"
    MODEL_POLICY = "model_policy"


@dataclass(frozen=True)
class AttributionDecision:
    group_id: str
    owner: str
    allowed_surfaces: Tuple[EvolutionSurface, ...]
    reason: str
    evolvable: bool = True

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "owner": self.owner,
            "allowed_surfaces": [surface.value for surface in self.allowed_surfaces],
            "reason": self.reason,
            "evolvable": self.evolvable,
        }


class CreditAttributor:
    """确定性归因先于 LLM 反思；安全/代码缺陷明确阻断自动进化。"""

    def attribute(self, cluster: BadCaseCluster) -> AttributionDecision:
        stages = {case.stage for case in cluster.cases}
        symptoms = " ".join(cluster.symptoms).lower()

        if stages & {BadCaseStage.INPUT_SECURITY, BadCaseStage.INFRASTRUCTURE}:
            return self._blocked(cluster, "security_or_infrastructure_owner", "安全或基础设施合同不得由 Agent 自动修改")
        if BadCaseStage.TOOL_POLICY in stages and any(
            token in symptoms for token in ("unauthorized", "outcome_unknown", "timeout", "cancel")
        ):
            return self._blocked(cluster, "tool_runtime_owner", "授权、取消和副作用终态属于生产代码/策略 Owner")
        if BadCaseStage.INTENT in stages:
            return AttributionDecision(
                cluster.group_id, "intent_recognizer",
                (EvolutionSurface.PROMPTS, EvolutionSurface.FEW_SHOTS),
                "意图误判只允许调整 intent Prompt 与经审核示例",
            )
        if stages & {BadCaseStage.ROUTING, BadCaseStage.PLANNING, BadCaseStage.COVERAGE}:
            return AttributionDecision(
                cluster.group_id, "task_planner",
                (EvolutionSurface.ROUTING,),
                "当前 TaskGraph 是确定性 Planner，自动候选只调整已接线的路由阈值",
            )
        if stages & {BadCaseStage.RETRIEVAL, BadCaseStage.MEMORY}:
            return AttributionDecision(
                cluster.group_id, "retrieval_policy",
                (EvolutionSurface.RETRIEVAL,),
                "召回或记忆干扰归因到已接线的 Top-K、RRF 与混合检索权重",
            )
        if BadCaseStage.TOOL_POLICY in stages:
            return AttributionDecision(
                cluster.group_id, "tool_selection",
                (EvolutionSurface.TOOL_DESCRIPTIONS, EvolutionSurface.PROMPTS),
                "仅工具选择错误可调整描述；权限和审批保持不可变",
            )
        if stages & {BadCaseStage.EXECUTION, BadCaseStage.SYNTHESIS, BadCaseStage.VERIFICATION}:
            return AttributionDecision(
                cluster.group_id, "worker_response",
                (EvolutionSurface.PROMPTS, EvolutionSurface.FEW_SHOTS),
                "有证据但执行/表达失败归因到 Worker 或 Synthesis Prompt",
            )
        return self._blocked(cluster, "unknown", "没有足够证据确定可进化 Owner")

    @staticmethod
    def _blocked(cluster: BadCaseCluster, owner: str, reason: str) -> AttributionDecision:
        return AttributionDecision(cluster.group_id, owner, (), reason, evolvable=False)
