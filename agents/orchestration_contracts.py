"""Legacy enum values still used by command and encoder evaluation.

Target WorkPlan and AgentResult are the execution contracts in application.
"""
from enum import Enum


class AgentType(str, Enum):
    """当前能力注册表支持的 Agent Owner。"""

    GENERAL = "general"
    TECHNICAL = "technical"
    BILLING = "billing"
    ACCOUNT_SECURITY = "account_security"
    ESCALATION = "escalation"


class TaskRisk(str, Enum):
    """子任务风险等级；高风险任务必须经过发布校验或人工接管。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskEffect(str, Enum):
    """计划阶段的副作用上界，由调度器决定是否可并行。"""

    READ_ONLY = "read_only"
    WRITE_REQUIRES_APPROVAL = "write_requires_approval"
