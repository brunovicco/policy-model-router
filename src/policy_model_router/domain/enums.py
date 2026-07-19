"""Closed vocabularies for model-routing decisions.

Mirrors the shape of ``credit_desk_contracts.enums`` in the ``multi-agent-credit-desk`` monorepo
(a separate repository; not imported here - see ``docs/adr/0001-clean-architecture.md`` and the
project README for why this service has no dependency on that monorepo).
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
