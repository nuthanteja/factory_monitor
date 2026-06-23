"""Unit tests: build_provider_chain + ProviderChain fall-through logic."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from cloud.notifications.chain import ProviderChain, build_provider_chain
from cloud.notifications.provider import NotificationKind, ProviderResult
from cloud.common.config import Settings


# ── helpers ────────────────────────────────────────────────────────────────────

def _sent(channel: str = "console") -> ProviderResult:
    return ProviderResult(sid=None, status="sent", channel=channel)


def _degraded(channel: str = "whatsapp") -> ProviderResult:
    return ProviderResult(sid=None, status="degraded", channel=channel)


def _failed(channel: str = "whatsapp") -> ProviderResult:
    return ProviderResult(sid=None, status="failed", channel=channel)


def _mock_provider(result: ProviderResult) -> AsyncMock:
    p = AsyncMock()
    p.send = AsyncMock(return_value=result)
    p.healthcheck = AsyncMock(return_value=True)
    return p


# ── ProviderChain tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_provider_succeeds_no_fallthrough():
    p1 = _mock_provider(_sent("whatsapp"))
    p2 = _mock_provider(_sent("sms"))
    chain = ProviderChain([p1, p2])

    result = await chain.send(
        "+10000000001",
        NotificationKind.TEMPLATE,
        template_name="alert_operator",
        variables={},
        idempotency_key="c-idem-001",
    )

    assert result.status == "sent"
    assert result.channel == "whatsapp"
    p1.send.assert_awaited_once()
    p2.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_degraded_falls_through_to_next():
    p1 = _mock_provider(_degraded("whatsapp"))
    p2 = _mock_provider(_sent("sms"))
    chain = ProviderChain([p1, p2])

    result = await chain.send(
        "+10000000001",
        NotificationKind.FREEFORM,
        body="some body",
        idempotency_key="c-idem-002",
    )

    assert result.status == "sent"
    assert result.channel == "sms"
    p2.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_falls_through_to_next():
    p1 = _mock_provider(_failed("whatsapp"))
    p2 = _mock_provider(_sent("console"))
    chain = ProviderChain([p1, p2])

    result = await chain.send(
        "+10000000001",
        NotificationKind.TEMPLATE,
        template_name="t",
        idempotency_key="c-idem-003",
    )

    assert result.status == "sent"
    assert result.channel == "console"


@pytest.mark.asyncio
async def test_all_providers_fail_returns_last_result():
    p1 = _mock_provider(_failed("whatsapp"))
    p2 = _mock_provider(_failed("sms"))
    chain = ProviderChain([p1, p2])

    result = await chain.send(
        "+10000000001",
        NotificationKind.TEMPLATE,
        template_name="t",
        idempotency_key="c-idem-004",
    )

    # Last provider's result is returned even on failure
    assert result.status == "failed"
    assert result.channel == "sms"


@pytest.mark.asyncio
async def test_empty_chain_raises():
    chain = ProviderChain([])
    with pytest.raises(ValueError, match="empty"):
        await chain.send("+1", NotificationKind.TEMPLATE, template_name="t", idempotency_key="x")


# ── build_provider_chain tests ─────────────────────────────────────────────────

def test_build_console_chain_default():
    settings = Settings(
        notify_provider_chain="console",
        database_url="postgresql+asyncpg://x:x@localhost/x",
    )
    from cloud.notifications.console import ConsoleProvider
    chain = build_provider_chain(settings)
    assert len(chain) == 1
    assert isinstance(chain[0], ConsoleProvider)


def test_build_chain_unknown_provider_raises():
    settings = Settings(
        notify_provider_chain="bogus",
        database_url="postgresql+asyncpg://x:x@localhost/x",
    )
    with pytest.raises(ValueError, match="bogus"):
        build_provider_chain(settings)


def test_build_whatsapp_sms_console_chain():
    settings = Settings(
        notify_provider_chain="whatsapp,sms,console",
        twilio_account_sid="ACtest",
        twilio_auth_token="token",
        twilio_whatsapp_from="+14155238886",
        twilio_sms_from="+15005550006",
        database_url="postgresql+asyncpg://x:x@localhost/x",
    )
    from cloud.notifications.twilio_whatsapp import TwilioWhatsAppProvider
    from cloud.notifications.twilio_sms import TwilioSmsProvider
    from cloud.notifications.console import ConsoleProvider
    chain = build_provider_chain(settings)
    assert len(chain) == 3
    assert isinstance(chain[0], TwilioWhatsAppProvider)
    assert isinstance(chain[1], TwilioSmsProvider)
    assert isinstance(chain[2], ConsoleProvider)


def test_build_whatsapp_without_twilio_creds_raises():
    settings = Settings(
        notify_provider_chain="whatsapp",
        database_url="postgresql+asyncpg://x:x@localhost/x",
        # no twilio_account_sid / auth_token
    )
    with pytest.raises(ValueError, match="TWILIO"):
        build_provider_chain(settings)
