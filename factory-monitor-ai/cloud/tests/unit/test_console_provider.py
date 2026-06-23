"""Unit tests: ConsoleProvider logs and returns a sent ProviderResult."""
from __future__ import annotations

import logging
import uuid

import pytest

from cloud.notifications.console import ConsoleProvider
from cloud.notifications.provider import NotificationKind, ProviderResult


@pytest.fixture
def provider() -> ConsoleProvider:
    return ConsoleProvider()


@pytest.mark.asyncio
async def test_send_template_returns_sent(provider: ConsoleProvider):
    result = await provider.send(
        "+10000000000",
        NotificationKind.TEMPLATE,
        template_name="alert_operator",
        variables={"incident_id": str(uuid.uuid4()), "zone": "zone_weld_bay"},
        idempotency_key="idem-001",
    )
    assert isinstance(result, ProviderResult)
    assert result.status == "sent"
    assert result.channel == "console"
    assert result.sid is None


@pytest.mark.asyncio
async def test_send_freeform_also_succeeds(provider: ConsoleProvider):
    """ConsoleProvider must accept FREEFORM without a session window check."""
    result = await provider.send(
        "+10000000000",
        NotificationKind.FREEFORM,
        body="The operator has been paged.",
        idempotency_key="idem-002",
    )
    assert result.status == "sent"
    assert result.channel == "console"


@pytest.mark.asyncio
async def test_send_logs_to_notification_logger(
    provider: ConsoleProvider, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.INFO, logger="factory_monitor.notifications.console"):
        await provider.send(
            "+19995550001",
            NotificationKind.TEMPLATE,
            template_name="alert_floor_manager",
            variables={"severity": "high"},
            idempotency_key="idem-003",
        )
    assert any("+19995550001" in r.message for r in caplog.records)
    assert any("alert_floor_manager" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_healthcheck_always_true(provider: ConsoleProvider):
    assert await provider.healthcheck() is True


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_still_returns_sent(provider: ConsoleProvider):
    """Console never errors on a repeated idempotency_key — idempotent by nature."""
    key = "idem-dup-001"
    r1 = await provider.send(
        "+10000000000",
        NotificationKind.TEMPLATE,
        template_name="t",
        idempotency_key=key,
    )
    r2 = await provider.send(
        "+10000000000",
        NotificationKind.TEMPLATE,
        template_name="t",
        idempotency_key=key,
    )
    assert r1.status == "sent"
    assert r2.status == "sent"
