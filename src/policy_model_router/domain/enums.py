"""Closed vocabularies for governed routing constraints and reason codes.

``DataClassification`` and ``RiskLevel`` remain controlled vocabularies. Workload and logical
model-group names are intentionally *not* enums in API 1.0; they are policy-defined identifiers
from :mod:`policy_model_router.domain.identifiers`.

``Workload`` and ``ModelGroup`` are re-exported as source-compatibility aliases for 0.x imports.
They are validated string identifiers and do not define the complete policy vocabulary.
"""

from enum import StrEnum

from policy_model_router.domain.identifiers import (
    ModelGroup,
    ModelGroupId,
    Workload,
    WorkloadId,
)


class DataClassification(StrEnum):
    """LGPD/LC 105-oriented sensitivity tier for a piece of data or an artifact."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RiskLevel(StrEnum):
    """Assessed risk level of the workflow issuing a routing request."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReasonCode(StrEnum):
    """Machine-readable reason a candidate model group was rejected, one per constraint."""

    DATA_CLASSIFICATION_NOT_AUTHORIZED = "data_classification_not_authorized"
    RISK_LEVEL_NOT_AUTHORIZED = "risk_level_not_authorized"
    STRUCTURED_OUTPUT_UNSUPPORTED = "structured_output_unsupported"
    TOOL_CALLING_UNSUPPORTED = "tool_calling_unsupported"
    CONTEXT_WINDOW_EXCEEDED = "context_window_exceeded"
    COST_CEILING_EXCEEDED = "cost_ceiling_exceeded"
    LATENCY_CEILING_EXCEEDED = "latency_ceiling_exceeded"
    MODEL_GROUP_UNAVAILABLE = "model_group_unavailable"
    AGENT_NOT_ALLOWED = "agent_not_allowed"
    WORKLOAD_MAPPED_ELSEWHERE = "workload_mapped_elsewhere"


__all__ = [
    "DataClassification",
    "ModelGroup",
    "ModelGroupId",
    "ReasonCode",
    "RiskLevel",
    "Workload",
    "WorkloadId",
]
