"""TwilioSmsProvider — SMS fallback when WhatsApp is unavailable.

No window policy: SMS has no 24h session restriction.  Both TEMPLATE and
FREEFORM are rendered as plain text body (SMS has no template API).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from cloud.notifications.provider import NotificationKind, ProviderResult

logger = logging.getLogger("factory_monitor.notifications.twilio_sms")


class TwilioSmsProvider:
    """Structural implementation of NotificationProvider for Twilio SMS."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        *,
        _twilio_client: Any = None,
    ) -> None:
        self._from = from_number
        if _twilio_client is not None:
            self._twilio_client = _twilio_client
        else:
            try:
                from twilio.rest import Client  # type: ignore[import]
                self._twilio_client = Client(account_sid, auth_token)
            except ImportError as exc:
                raise RuntimeError(
                    "twilio package is not installed; install it or use ConsoleProvider"
                ) from exc

    def _render_body(
        self,
        kind: NotificationKind,
        *,
        template_name: str | None,
        variables: dict | None,
        body: str | None,
    ) -> str:
        if kind is NotificationKind.FREEFORM:
            return body or ""
        # TEMPLATE: compose a plain-text rendering
        rendered = template_name or ""
        if variables:
            rendered += " " + " ".join(f"{k}={v}" for k, v in variables.items())
        return rendered

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
        text = self._render_body(
            kind, template_name=template_name, variables=variables, body=body
        )
        try:
            msg = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._twilio_client.messages.create(
                    from_=self._from,
                    to=to,
                    body=text,
                ),
            )
            logger.info(
                "SMS sent to %s sid=%s idem=%s", to[-4:], msg.sid, idempotency_key
            )
            return ProviderResult(sid=msg.sid, status="sent", channel="sms")
        except Exception as exc:
            logger.error(
                "SMS send failed to %s idem=%s err=%s", to[-4:], idempotency_key, exc
            )
            return ProviderResult(
                sid=None, status="failed", channel="sms", error=str(exc)
            )

    async def healthcheck(self) -> bool:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._twilio_client.api(
                    self._twilio_client.account_sid
                ),
            )
            return True
        except Exception:
            return False
