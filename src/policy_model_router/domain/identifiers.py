"""Policy-defined identifiers for workloads and logical model groups.

Unlike data classification and risk level, workloads and model groups are extension points owned by
the active routing policy. They are validated identifiers, not closed Python enums.
"""

import re
from typing import ClassVar, Self

POLICY_IDENTIFIER_PATTERN = r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$"
_POLICY_IDENTIFIER_RE = re.compile(POLICY_IDENTIFIER_PATTERN)


class PolicyIdentifier(str):
    """Validated lowercase identifier declared by routing policy."""

    kind: ClassVar[str] = "policy identifier"

    def __new__(cls, value: str) -> Self:
        """Validate and construct one immutable policy identifier."""
        if not isinstance(value, str):
            raise TypeError(f"{cls.kind} must be a string")
        if not _POLICY_IDENTIFIER_RE.fullmatch(value):
            raise ValueError(
                f"{cls.kind} must be 1-128 lowercase characters using only "
                "letters, digits, '.', '_' or '-', and must start/end with a letter or digit"
            )
        return str.__new__(cls, value)

    @property
    def value(self) -> str:
        """Return the wire/config representation of this identifier."""
        return str(self)


class WorkloadId(PolicyIdentifier):
    """Policy-defined workload identifier."""

    kind = "workload identifier"

    # 0.x compatibility constants. They are values, not the complete workload vocabulary.
    DOCUMENT_EXTRACTION: ClassVar["WorkloadId"]
    CASHFLOW_ANALYSIS: ClassVar["WorkloadId"]
    FINDINGS_CORRELATION: ClassVar["WorkloadId"]
    OPINION_DRAFTING: ClassVar["WorkloadId"]
    JSON_REPAIR: ClassVar["WorkloadId"]


class ModelGroupId(PolicyIdentifier):
    """Policy-defined logical model-group identifier."""

    kind = "model-group identifier"

    # 0.x compatibility constants. They are values, not the complete model-group vocabulary.
    FAST_SMALL: ClassVar["ModelGroupId"]
    REASONING_MEDIUM: ClassVar["ModelGroupId"]
    REASONING_STRONG: ClassVar["ModelGroupId"]
    FAST_STRUCTURED_OUTPUT: ClassVar["ModelGroupId"]


WorkloadId.DOCUMENT_EXTRACTION = WorkloadId("document_extraction")
WorkloadId.CASHFLOW_ANALYSIS = WorkloadId("cashflow_analysis")
WorkloadId.FINDINGS_CORRELATION = WorkloadId("findings_correlation")
WorkloadId.OPINION_DRAFTING = WorkloadId("opinion_drafting")
WorkloadId.JSON_REPAIR = WorkloadId("json_repair")

ModelGroupId.FAST_SMALL = ModelGroupId("fast-small")
ModelGroupId.REASONING_MEDIUM = ModelGroupId("reasoning-medium")
ModelGroupId.REASONING_STRONG = ModelGroupId("reasoning-strong")
ModelGroupId.FAST_STRUCTURED_OUTPUT = ModelGroupId("fast-structured-output")

# Source-compatibility aliases for 0.x imports. New code should use WorkloadId/ModelGroupId.
Workload = WorkloadId
ModelGroup = ModelGroupId

__all__ = [
    "ModelGroup",
    "ModelGroupId",
    "POLICY_IDENTIFIER_PATTERN",
    "PolicyIdentifier",
    "Workload",
    "WorkloadId",
]
