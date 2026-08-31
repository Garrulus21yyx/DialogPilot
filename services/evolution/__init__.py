"""Agent 策略进化控制面。

运行链路只消费已经注册并固定的不可变 Bundle；学习链路只能产生候选，不能
直接改写生产 Prompt、权限或安全边界。
"""

from .bundle import AgentBundle, BundleContractError, build_default_bundle
from .envelope import EvolutionEnvelope
from .registry import AgentBundleRegistry, BundleConflictError, BundleNotFoundError

__all__ = [
    "AgentBundle",
    "AgentBundleRegistry",
    "BundleConflictError",
    "BundleContractError",
    "BundleNotFoundError",
    "EvolutionEnvelope",
    "build_default_bundle",
]
