"""Policy-defined identifiers for workloads and logical model groups.

Unlike data classification and risk level, workloads and model groups are extension points owned by
the active routing policy. They are validated identifiers, not closed Python enums. New workloads
are namespace-qualified (for example ``rag.answer``); the five 0.x credit-desk workload names remain
valid compatibility identifiers during migration.
"""

import re
from collections.abc import Iterator
from typing import ClassVar, Self

POLICY_IDENTIFIER_PATTERN = r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$"
_POLICY_IDENTIFIER_RE = re.compile(POLICY_IDENTIFIER_PATTERN)
_LEGACY_WORKLOAD_IDENTIFIERS = frozenset(
    {
        "document_extraction",
        "cashflow_analysis",
        "findings_correlation",
        "opinion_drafting",
        "json_repair",
    }
)


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


class _LegacyIdentifierMeta(type):
    """Expose 0.x compatibility constants as an iterable without closing the vocabulary."""

    def __iter__(cls) -> Iterator[PolicyIdentifier]:
        """Iterate only over compatibility constants declared directly on the identifier class."""
        return (
            value
            for name, value in vars(cls).items()
            if name.isupper() and isinstance(value, cls)
        )


class WorkloadId(PolicyIdentifier, metaclass=_LegacyIdentifierMeta):
    """Policy-defined, namespace-qualified workload identifier."""

    kind = "workload identifier"

    # 0.x compatibility constants. They are values, not the complete workload vocabulary.
    DOCUMENT_EXTRACTION: ClassVar["WorkloadId"]
    CASHFLOW_ANALYSIS: ClassVar["WorkloadId"]
    FINDINGS_CORRELATION: ClassVar["WorkloadId"]
    OPINION_DRAFTING: ClassVar["WorkloadId"]
    JSON_REPAIR: ClassVar["WorkloadId"]

    def __new__(cls, value: str) -> Self:
        """Require namespace qualification for new workloads while preserving 0.x names."""
        identifier = super().__new__(cls, value)
        if "." not in identifier and identifier not in _LEGACY_WORKLOAD_IDENTIFIERS:
            raise ValueError(
                "new workload identifiers must be namespace-qualified with '.', for example "
                "'rag.answer'"
            )
        return identifier


class ModelGroupId(PolicyIdentifier, metaclass=_LegacyIdentifierMeta):
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
