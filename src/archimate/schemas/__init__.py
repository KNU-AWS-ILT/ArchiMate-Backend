"""팀 공유 계약(스키마). 프론트·비용 계산·LLM이 모두 이 모델을 기준으로 한다.

JSON 필드명은 camelCase, 파이썬 속성명은 snake_case (base.CamelModel).
"""

from archimate.schemas.api import (
    CostItem,
    CreateDesignRequest,
    DesignJob,
    DesignJobAccepted,
    DesignResult,
    ErrorCode,
    ErrorResponse,
    JobStatus,
    MonthlyCost,
)
from archimate.schemas.architecture import (
    REQUIRED_USAGE_FIELDS,
    TIER_FAILURE_REASONS,
    Architecture,
    Design,
    DesignDraft,
    DesignSet,
    Difficulty,
    Edge,
    Group,
    GroupKind,
    Node,
    TierFailure,
    Usage,
)
from archimate.schemas.base import CamelModel
from archimate.schemas.common import (
    PRICED_SERVICES,
    SERVICE_INFO,
    AwsService,
    ServiceCategory,
    Tier,
)
from archimate.schemas.explanation import (
    Alternative,
    AlternativeDraft,
    ExplanationDraft,
    NodeExplanation,
)
from archimate.schemas.operations import Checklist, GuidePhase, GuideStep, OperationsGuide
from archimate.schemas.requirements import Requirements

__all__ = [
    "PRICED_SERVICES",
    "REQUIRED_USAGE_FIELDS",
    "SERVICE_INFO",
    "Alternative",
    "AlternativeDraft",
    "Architecture",
    "AwsService",
    "CamelModel",
    "Checklist",
    "CostItem",
    "CreateDesignRequest",
    "Design",
    "DesignDraft",
    "DesignJob",
    "DesignJobAccepted",
    "DesignResult",
    "DesignSet",
    "Difficulty",
    "Edge",
    "ErrorCode",
    "ErrorResponse",
    "ExplanationDraft",
    "GuidePhase",
    "GuideStep",
    "Group",
    "GroupKind",
    "JobStatus",
    "MonthlyCost",
    "Node",
    "NodeExplanation",
    "OperationsGuide",
    "Requirements",
    "ServiceCategory",
    "Tier",
    "TIER_FAILURE_REASONS",
    "TierFailure",
    "Usage",
]
