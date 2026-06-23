"""Unit tests: TwilioWhatsAppProvider template/free-form and 24h window policy.

The Twilio REST client is replaced with a synchronous stub so no network calls
are made. Window state is passed in directly to avoid a real DB dependency in
unit tests (the integration test in Task 14 will use testcontainers).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cloud.notifications.provider import NotificationKind, ProviderResult
from cloud.notifications.twilio_whatsapp import TwilioWhatsAppProvider


def _make_provider(window_open: bool = False) -> tuple[TwilioWhatsAppProvider, MagicMock]:
    """Return (provider, mock_twilio_messages_create) with window state injected."""
    mock_messages = MagicMock()
    mock_message_obj = MagicMock()
    mock_message_obj.sid = "WA_SID_001"
    mock_messages.create.return_value = mock_message_obj

    mock_client = MagicMock()
    mock_client.messages = mock_messages

    # window_open controls whether _has_open_window returns True
    provider = TwilioWhatsAppProvider(
        account_sid="ACtest",
        auth_token="token",
        from_number="+14155238886",
        _twilio_client=mock_client,
    )
    # Patch the window check directly for unit isolation
    provider._has_open_window = AsyncMock(return_value=window_open)

    return provider, mock_messages


@pytest.mark.asyncio
async def test_template_send_succeeds_without_open_window():
    """TEMPLATE sends must NOT require an open window — window-independent per §7."""
    provider, mock_msgs = _make_provider(window_open=False)
    result = await provider.send(
        "+10000000001",
        NotificationKind.TEMPLATE,
        template_name="alert_operator",
        variables={"incident_id": str(uuid.uuid4()), "zone": "weld_bay"},
        idempotency_key="idem-t-001",
    )
    assert result.status == "sent"
    assert result.channel == "whatsapp"
    assert result.sid == "WA_SID_001"
    mock_msgs.create.assert_called_once()
    call_kwargs = mock_msgs.create.call_args.kwargs
    # "alert_operator" does NOT start with "HX", so the demo path renders a body
    assert "body" in call_kwargs, f"Expected 'body' key in create kwargs, got: {list(call_kwargs)}"
    # The to number must be prefixed with whatsapp: scheme
    assert call_kwargs["to"].startswith("whatsapp:")


@pytest.mark.asyncio
async def test_freeform_send_with_open_window_succeeds():
    """FREEFORM is allowed inside the 24h window."""
    provider, mock_msgs = _make_provider(window_open=True)
    result = await provider.send(
        "+10000000001",
        NotificationKind.FREEFORM,
        body="Incident has been escalated to tier 1.",
        idempotency_key="idem-f-001",
    )
    assert result.status == "sent"
    assert result.channel == "whatsapp"
    mock_msgs.create.assert_called_once()


@pytest.mark.asyncio
async def test_freeform_without_open_window_auto_downgrades_to_sms_channel():
    """FREEFORM with no window must NOT attempt a free-form WhatsApp send.

    The provider must degrade: return status='degraded' so the chain falls
    to the next provider (TwilioSmsProvider), rather than silently losing the
    message or raising.
    """
    provider, mock_msgs = _make_provider(window_open=False)
    result = await provider.send(
        "+10000000001",
        NotificationKind.FREEFORM,
        body="Some free-form body that has no window.",
        idempotency_key="idem-f-002",
    )
    # Must NOT have called Twilio (would violate the 24h policy)
    mock_msgs.create.assert_not_called()
    # Must signal degradation so the chain can fall through
    assert result.status == "degraded"
    assert result.channel == "whatsapp"


@pytest.mark.asyncio
async def test_twilio_api_error_returns_failed_result():
    """Any Twilio exception must be caught and returned as status='failed'."""
    provider, mock_msgs = _make_provider(window_open=True)
    mock_msgs.create.side_effect = Exception("Twilio 429 rate limit")

    result = await provider.send(
        "+10000000001",
        NotificationKind.TEMPLATE,
        template_name="alert_operator",
        variables={},
        idempotency_key="idem-err-001",
    )
    assert result.status == "failed"
    assert result.channel == "whatsapp"
    assert result.sid is None


@pytest.mark.asyncio
async def test_healthcheck_returns_true_when_client_works():
    provider, _ = _make_provider()
    provider._twilio_client.api.accounts.return_value.fetch.return_value = MagicMock()
    assert await provider.healthcheck() is True


@pytest.mark.asyncio
async def test_healthcheck_returns_false_on_exception():
    provider, _ = _make_provider()
    provider._twilio_client.api.accounts.side_effect = Exception("auth failure")
    result = await provider.healthcheck()
    assert result is False
