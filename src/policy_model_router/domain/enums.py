"""Closed vocabularies for model-routing decisions.

``DataClassification``, ``RiskLevel``, ``Workload``, and ``ModelGroup`` mirror the shape of
``credit_desk_contracts.enums`` in the ``multi-agent-credit-desk`` monorepo (a separate repository;
not imported here - see ``docs/adr/0001-clean-architecture.md`` and the project README for why this
service has no dependency on that monorepo). ``ReasonCode`` is this router's own vocabulary, not
part of that mirror - see ADR-0009's provenance rationale and ``entrypoints/contracts.py``'s
module docstring for other deliberate, documented divergences from the mirrored shape.
"""

from enum import StrEnum


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


class Workload(StrEnum):
    """LLM workload kind, used as the routing key from workload to model group."""

    DOCUMENT_EXTRACTION = "document_extraction"
    CASHFLOW_ANALYSIS = "cashflow_analysis"
    FINDINGS_CORRELATION = "findings_correlation"
    OPINION_DRAFTING = "opinion_drafting"
    JSON_REPAIR = "json_repair"


class ModelGroup(StrEnum):
    """Model group selectable by the router, independent of provider or deployment."""

    FAST_SMALL = "fast-small"
    REASONING_MEDIUM = "reasoning-medium"
    REASONING_STRONG = "reasoning-strong"
    FAST_STRUCTURED_OUTPUT = "fast-structured-output"


class ReasonCode(StrEnum):
    """Machine-readable reason a candidate model group was rejected, one per constraint.

    Each value corresponds one-to-one with a predicate in ``domain/constraints.py::CONSTRAINTS``
    (in the same order), plus ``WORKLOAD_MAPPED_ELSEWHERE`` for a candidate that passed every
    constraint but simply isn't the workload's declaratively mapped group.
    """

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
