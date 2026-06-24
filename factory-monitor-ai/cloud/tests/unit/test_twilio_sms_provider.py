"""Unit tests: TwilioSmsProvider — SMS fallback, no window policy."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from cloud.notifications.provider import NotificationKind
from cloud.notifications.twilio_sms import TwilioSmsProvider


def _make_provider(sid: str = "SM_001") -> tuple[TwilioSmsProvider, MagicMock]:
    mock_message_obj = MagicMock()
    mock_message_obj.sid = sid
    mock_messages = MagicMock()
    mock_messages.create.return_value = mock_message_obj

    mock_client = MagicMock()
    mock_client.messages = mock_messages

    provider = TwilioSmsProvider(
        account_sid="ACtest",
        auth_token="token",
        from_number="+15005550006",
        _twilio_client=mock_client,
    )
    return provider, mock_messages


@pytest.mark.asyncio
async def test_send_template_via_sms_renders_body():
    provider, mock_msgs = _make_provider()
    result = await provider.send(
        "+10000000002",
        NotificationKind.TEMPLATE,
        template_name="alert_operator",
        variables={"incident_id": str(uuid.uuid4())},
        idempotency_key="sms-idem-001",
    )
    assert result.status == "sent"
    assert result.channel == "sms"
    assert result.sid == "SM_001"
    mock_msgs.create.assert_called_once()
    call_kw = mock_msgs.create.call_args.kwargs
    # SMS must NOT use whatsapp: prefix
    assert not call_kw["to"].startswith("whatsapp:")
    assert not call_kw["from_"].startswith("whatsapp:")


@pytest.mark.asyncio
async def test_send_freeform_via_sms_sends_body():
    """SMS accepts FREEFORM without any window check — no 24h restriction on SMS."""
    provider, mock_msgs = _make_provider()
    result = await provider.send(
        "+10000000002",
        NotificationKind.FREEFORM,
        body="Escalation: Tier 1 — floor manager please respond.",
        idempotency_key="sms-idem-002",
    )
    assert result.status == "sent"
    assert result.channel == "sms"
    mock_msgs.create.assert_called_once()


@pytest.mark.asyncio
async def test_twilio_exception_returns_failed():
    provider, mock_msgs = _make_provider()
    mock_msgs.create.side_effect = Exception("Twilio 500")
    result = await provider.send(
        "+10000000002",
        NotificationKind.TEMPLATE,
        template_name="alert_operator",
        variables={},
        idempotency_key="sms-idem-err",
    )
    assert result.status == "failed"
    assert result.channel == "sms"
    assert result.sid is None


@pytest.mark.asyncio
async def test_healthcheck_true_when_client_works():
    provider, _ = _make_provider()
    provider._twilio_client.api.accounts.return_value.fetch.return_value = MagicMock()
    assert await provider.healthcheck() is True


@pytest.mark.asyncio
async def test_healthcheck_false_on_exception():
    provider, _ = _make_provider()
    provider._twilio_client.api.accounts.side_effect = Exception("auth")
    assert await provider.healthcheck() is False
