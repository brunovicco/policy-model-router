"""Verify one Governance runtime authorization against a domain routing request.

The verification order is fixed and each step fails closed: identity and time, trusted key,
Ed25519 signature over canonical claims, request binding, agent binding, policy provenance,
runtime control, then single-use consumption. ``require_selected_model`` runs afterwards, once the
routing decision exists.

This module takes the domain :class:`RouteRequest`, not the Pydantic wire model: whether a request
arrived over HTTP is irrelevant to whether Governance authorized it, and depending on the transport
schema here would invert the dependency rule (ADR-0001).
"""

import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from cryptography.exceptions import InvalidSignature

from policy_model_router.application.runtime_authorization_contract import (
    RuntimeAuthorizationClaims,
    SignedRuntimeAuthorization,
)
from policy_model_router.application.runtime_control import RuntimeControlEnforcer
from policy_model_router.domain.identifiers import ModelGroupId
from policy_model_router.domain.routing import RouteRequest
from policy_model_router.domain.runtime_authorization import (
    RuntimeAuthorizationError,
    RuntimeAuthorizationKeyStatus,
    TrustedRuntimeAuthorizationKey,
    TrustedRuntimeAuthorizationKeySet,
    decode_signature,
    require_utc,
)
from policy_model_router.domain.runtime_control import RuntimeControlEnforcementError


@dataclass(frozen=True, slots=True)
class VerifiedRuntimeAuthorization:
    """Cryptographically verified artifact bound to one route request."""

    envelope: SignedRuntimeAuthorization
    verified_at: datetime
    key_set_generation: int

    @property
    def authorization_id(self) -> UUID:
        """Return the single-use authorization identifier."""
        return self.envelope.claims.authorization_id


class RuntimeAuthorizationReplayGuard(Protocol):
    """Atomic replay-consumption boundary."""

    async def consume(
        self,
        authorization_id: UUID,
        *,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        """Return whether the ID was newly consumed."""
        ...

    async def ping(self) -> None:
        """Raise when replay state is unavailable."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


class RuntimeAuthorizationVerifier:
    """Verify signature, request binding, agent binding and one-time use."""

    def __init__(
        self,
        *,
        key_set: TrustedRuntimeAuthorizationKeySet,
        replay_guard: RuntimeAuthorizationReplayGuard,
        issuer: str,
        audience: str,
        agent_bindings: dict[str, UUID],
        expected_policy_id: str,
        expected_policy_version: str,
        expected_policy_digest: str,
        expected_control_catalog_id: str,
        expected_control_catalog_version: str,
        expected_control_catalog_digest: str,
        runtime_control: RuntimeControlEnforcer | None = None,
        clock_skew_seconds: int = 0,
    ) -> None:
        """Bind the trust set, replay guard, and expected Governance provenance to verify."""
        if not issuer or not audience:
            raise ValueError("Runtime authorization issuer and audience are required")
        if not 0 <= clock_skew_seconds <= 60:
            raise ValueError("clock_skew_seconds must be between 0 and 60")
        if not agent_bindings:
            raise ValueError("At least one runtime authorization agent binding is required")
        self._key_set = key_set
        self._replay_guard = replay_guard
        self._issuer = issuer
        self._audience = audience
        self._agent_bindings = dict(agent_bindings)
        self._expected_policy_id = expected_policy_id
        self._expected_policy_version = expected_policy_version
        self._expected_policy_digest = expected_policy_digest
        self._expected_control_catalog_id = expected_control_catalog_id
        self._expected_control_catalog_version = expected_control_catalog_version
        self._expected_control_catalog_digest = expected_control_catalog_digest
        self._runtime_control = runtime_control
        self._clock_skew = timedelta(seconds=clock_skew_seconds)

    async def verify(
        self,
        envelope: SignedRuntimeAuthorization,
        request: RouteRequest,
        *,
        now: datetime,
    ) -> VerifiedRuntimeAuthorization:
        """Verify and atomically consume one authorization."""
        require_utc(now)
        claims = envelope.claims
        self._verify_identity_and_time(claims, now)
        key = self._require_key(envelope, now)
        self._verify_signature(envelope, key)
        self._verify_request_binding(claims, request)
        self._verify_agent_binding(claims, request.agent_name)
        self._verify_policy_provenance(claims)

        if self._runtime_control is not None:
            try:
                await self._runtime_control.enforce(
                    agent_id=claims.subject.agent_id,
                    agent_version=claims.subject.agent_version,
                )
            except RuntimeControlEnforcementError as exc:
                raise RuntimeAuthorizationError(
                    exc.code,
                    str(exc),
                    authorization_verified=True,
                ) from exc

        try:
            fresh = await self._replay_guard.consume(
                claims.authorization_id,
                expires_at=claims.expires_at,
                now=now,
            )
        except RuntimeAuthorizationError:
            raise
        except Exception as exc:
            raise RuntimeAuthorizationError(
                "replay_store_unavailable",
                "Runtime authorization replay state is unavailable",
            ) from exc
        if not fresh:
            raise RuntimeAuthorizationError(
                "replay_detected",
                "Runtime authorization identifier was already consumed",
            )
        return VerifiedRuntimeAuthorization(
            envelope=envelope,
            verified_at=now,
            key_set_generation=self._key_set.generation,
        )

    def require_selected_model(
        self,
        verified: VerifiedRuntimeAuthorization,
        selected_model_group: ModelGroupId,
    ) -> None:
        """Require the Router-selected group to be explicitly signed by Governance."""
        claims = verified.envelope.claims
        matching = tuple(
            model
            for model in claims.scope.models
            if model.routing_group == selected_model_group.value
        )
        if not matching:
            raise RuntimeAuthorizationError(
                "selected_model_group_not_authorized",
                "Selected model group is outside the signed Governance scope",
            )
        if not any(
            claims.scope.data_classification in model.allowed_data_classes for model in matching
        ):
            raise RuntimeAuthorizationError(
                "selected_model_data_class_not_authorized",
                "Selected model group is not signed for this data classification",
            )

    async def ping(self) -> None:
        """Require replay and Runtime Control state to be reachable."""
        await self._replay_guard.ping()
        if self._runtime_control is not None:
            try:
                await self._runtime_control.ping()
            except RuntimeControlEnforcementError as exc:
                raise RuntimeAuthorizationError(exc.code, str(exc)) from exc

    async def close(self) -> None:
        """Release replay and Runtime Control resources."""
        await self._replay_guard.close()
        if self._runtime_control is not None:
            await self._runtime_control.close()

    def _verify_identity_and_time(
        self,
        claims: RuntimeAuthorizationClaims,
        now: datetime,
    ) -> None:
        if claims.issuer != self._issuer:
            raise RuntimeAuthorizationError(
                "issuer_mismatch",
                "Runtime authorization issuer is not trusted",
            )
        if self._audience not in claims.audience:
            raise RuntimeAuthorizationError(
                "audience_mismatch",
                "Runtime authorization audience does not include this service",
            )
        if claims.issued_at > now + self._clock_skew:
            raise RuntimeAuthorizationError(
                "issued_in_future",
                "Runtime authorization was issued in the future",
            )
        if claims.not_before > now + self._clock_skew:
            raise RuntimeAuthorizationError(
                "not_yet_valid",
                "Runtime authorization is not valid yet",
            )
        if claims.expires_at <= now - self._clock_skew:
            raise RuntimeAuthorizationError(
                "expired",
                "Runtime authorization has expired",
            )

    def _require_key(
        self,
        envelope: SignedRuntimeAuthorization,
        now: datetime,
    ) -> TrustedRuntimeAuthorizationKey:
        key = self._key_set.resolve(envelope.protected.kid)
        if key is None:
            raise RuntimeAuthorizationError(
                "unknown_key",
                "Runtime authorization key is not trusted",
            )
        if key.status is RuntimeAuthorizationKeyStatus.REVOKED:
            raise RuntimeAuthorizationError(
                "key_revoked",
                "Runtime authorization key is revoked",
            )
        if not key.not_before <= envelope.claims.issued_at < key.verify_until:
            raise RuntimeAuthorizationError(
                "key_not_valid_for_issue_time",
                "Authorization issue time is outside the key trust window",
            )
        if now - self._clock_skew >= key.verify_until:
            raise RuntimeAuthorizationError(
                "key_verification_window_closed",
                "Runtime authorization key verification window is closed",
            )
        return key

    @staticmethod
    def _verify_signature(
        envelope: SignedRuntimeAuthorization,
        key: TrustedRuntimeAuthorizationKey,
    ) -> None:
        try:
            signature = decode_signature(envelope.signature)
            key.public_key.verify(signature, envelope.signing_bytes())
        except (InvalidSignature, ValueError, binascii.Error) as exc:
            raise RuntimeAuthorizationError(
                "invalid_signature",
                "Runtime authorization signature is invalid",
            ) from exc

    @staticmethod
    def _verify_request_binding(
        claims: RuntimeAuthorizationClaims,
        request: RouteRequest,
    ) -> None:
        signed = claims.request
        cost_micros = _cost_micros(request.max_cost_usd)
        mismatches = {
            "requested_at": claims.issued_at != request.requested_at,
            "workflow_id": signed.workflow_id != request.workflow_id,
            "task_id": signed.task_id != request.task_id,
            "workload": signed.workload != request.workload.value,
            "context_tokens_estimated": (
                signed.context_tokens_estimated != request.context_tokens_estimated
            ),
            "max_output_tokens_estimated": (
                signed.max_output_tokens_estimated != request.max_output_tokens_estimated
            ),
            "structured_output_required": (
                signed.structured_output_required != request.structured_output_required
            ),
            "max_latency_ms": signed.max_latency_ms != request.max_latency_ms,
            "max_cost_usd_micros": signed.max_cost_usd_micros != cost_micros,
            "risk_level": claims.scope.risk_tier.value != request.risk_level.value,
            "data_classification": (
                claims.scope.data_classification != request.data_classification
            ),
        }
        failed = tuple(sorted(name for name, mismatch in mismatches.items() if mismatch))
        if failed:
            raise RuntimeAuthorizationError(
                "request_binding_mismatch",
                "Route request differs from signed Governance authorization: " + ", ".join(failed),
            )

    def _verify_agent_binding(
        self,
        claims: RuntimeAuthorizationClaims,
        agent_name: str,
    ) -> None:
        expected_agent_id = self._agent_bindings.get(agent_name)
        if expected_agent_id is None or expected_agent_id != claims.subject.agent_id:
            raise RuntimeAuthorizationError(
                "agent_binding_mismatch",
                "Route agent identity does not match signed Governance subject",
            )

    def _verify_policy_provenance(
        self,
        claims: RuntimeAuthorizationClaims,
    ) -> None:
        policy = claims.policy
        if (
            policy.policy_id != self._expected_policy_id
            or policy.policy_version != self._expected_policy_version
            or policy.policy_digest != self._expected_policy_digest
            or policy.control_catalog_id != self._expected_control_catalog_id
            or policy.control_catalog_version != self._expected_control_catalog_version
            or policy.control_catalog_digest != self._expected_control_catalog_digest
        ):
            raise RuntimeAuthorizationError(
                "governance_policy_mismatch",
                "Runtime authorization provenance is not trusted by this deployment",
            )


def _cost_micros(value: Decimal) -> int:
    micros = value * Decimal(1_000_000)
    integral = micros.to_integral_value()
    if micros != integral:
        raise RuntimeAuthorizationError(
            "request_cost_precision_unsupported",
            "Route cost cannot be represented as integer USD micros",
        )
    return int(integral)


__all__ = [
    "RuntimeAuthorizationReplayGuard",
    "RuntimeAuthorizationVerifier",
    "VerifiedRuntimeAuthorization",
]
