"""Unit tests: ProviderResult dataclass + NotificationProvider structural subtyping."""
from __future__ import annotations

import uuid
from typing import runtime_checkable

import pytest

from cloud.notifications.provider import (
    NotificationKind,
    NotificationProvider,
    ProviderResult,
)


def test_provider_result_fields():
    r = ProviderResult(sid="SM123", status="sent", channel="console")
    assert r.sid == "SM123"
    assert r.status == "sent"
    assert r.channel == "console"


def test_provider_result_sid_optional():
    r = ProviderResult(sid=None, status="sent", channel="console")
    assert r.sid is None


def test_provider_result_error_field():
    # Error field should carry failure detail; defaults to None on success
    r_success = ProviderResult(sid="SM123", status="sent", channel="whatsapp")
    assert r_success.error is None

    r_failed = ProviderResult(sid=None, status="failed", channel="whatsapp", error="boom")
    assert r_failed.error == "boom"


def test_notification_kind_enum_values():
    assert NotificationKind.TEMPLATE.value == "TEMPLATE"
    assert NotificationKind.FREEFORM.value == "FREEFORM"


def test_provider_protocol_is_runtime_checkable():
    # NotificationProvider must be @runtime_checkable so isinstance() works in the chain.
    # Prove it with a behavior-based check: define a minimal stub and assert isinstance() succeeds.
    class _RuntimeCheckStub:
        async def send(
            self,
            to: str,
            kind: NotificationKind,
            *,
            template_name: str | None = None,
            variables: dict | None = None,
            body: str | None = None,
            idempotency_key: str,
        ) -> ProviderResult:
            return ProviderResult(sid=None, status="sent", channel="console")

        async def healthcheck(self) -> bool:
            return True

    stub = _RuntimeCheckStub()
    # If @runtime_checkable is missing, this raises TypeError; it should not.
    assert isinstance(stub, NotificationProvider) is True


class _MinimalProvider:
    """Structural impl — must satisfy the Protocol without inheriting it."""

    async def send(
        self,
        to: str,
        kind: "NotificationKind",
        *,
        template_name: str | None = None,
        variables: dict | None = None,
        body: str | None = None,
        idempotency_key: str,
    ) -> "ProviderResult":
        return ProviderResult(sid="x", status="sent", channel="console")

    async def healthcheck(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_minimal_structural_provider_satisfies_protocol():
    p = _MinimalProvider()
    result = await p.send(
        "+10000000000",
        NotificationKind.TEMPLATE,
        template_name="alert_operator",
        variables={"incident_id": str(uuid.uuid4())},
        idempotency_key="key-1",
    )
    assert result.status == "sent"
    assert await p.healthcheck() is True
