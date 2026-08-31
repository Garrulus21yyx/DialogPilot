"""Agent 策略进化控制面。

运行链路只消费已经注册并固定的不可变 Bundle；学习链路只能产生候选，不能
直接改写生产 Prompt、权限或安全边界。
"""

from .bundle import AgentBundle, BundleContractError, build_default_bundle
from .envelope import EvolutionEnvelope
from .registry import AgentBundleRegistry, BundleConflictError, BundleNotFoundError
from .attribution import AttributionDecision, CreditAttributor, EvolutionSurface
from .miner import BadCaseCluster, BadCaseMiner
from .proposal_generator import GEPALiteProposalGenerator, build_llm_proposal_generator

__all__ = [
    "AgentBundle",
    "AgentBundleRegistry",
    "BundleConflictError",
    "BundleContractError",
    "BundleNotFoundError",
    "EvolutionEnvelope",
    "AttributionDecision",
    "BadCaseCluster",
    "BadCaseMiner",
    "CreditAttributor",
    "EvolutionSurface",
    "GEPALiteProposalGenerator",
    "build_default_bundle",
    "build_llm_proposal_generator",
]
