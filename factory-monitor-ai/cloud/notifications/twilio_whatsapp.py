"""TwilioWhatsAppProvider — sends via Twilio WhatsApp, encodes §7 window policy.

Policy (from spec §7):
  TEMPLATE  → always allowed (pre-approved, window-independent).
              Calls Twilio content template API.
  FREEFORM  → only inside an open 24h whatsapp_sessions window.
              If no open window → return status='degraded' so the provider
              chain falls through to TwilioSmsProvider or ConsoleProvider.
              Never raises; all Twilio exceptions are caught and returned as
              status='failed'.

The optional `_twilio_client` constructor param replaces the real client in
unit tests. The `session_maker` param provides DB access for window checks in
integration; it can be None when injecting a mock `_has_open_window`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from cloud.notifications.provider import NotificationKind, ProviderResult

logger = logging.getLogger("factory_monitor.notifications.twilio_whatsapp")

_WA_PREFIX = "whatsapp:"


class TwilioWhatsAppProvider:
    """Structural implementation of NotificationProvider for Twilio WhatsApp."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        *,
        session_maker: Any = None,
        _twilio_client: Any = None,
    ) -> None:
        self._from = f"{_WA_PREFIX}{from_number}"
        self._session_maker = session_maker

        if _twilio_client is not None:
            self._twilio_client = _twilio_client
        else:
            # Lazy import so missing twilio SDK doesn't break ConsoleProvider usage.
            try:
                from twilio.rest import Client  # type: ignore[import]
                self._twilio_client = Client(account_sid, auth_token)
            except ImportError as exc:
                raise RuntimeError(
                    "twilio package is not installed; install it or use ConsoleProvider"
                ) from exc

    async def _has_open_window(self, phone: str) -> bool:
        """Return True iff whatsapp_sessions has an un-expired row for phone."""
        if self._session_maker is None:
            return False
        from sqlalchemy import select
        from cloud.common.db.models import WhatsappSession

        async with self._session_maker() as session:
            stmt = select(WhatsappSession).where(
                WhatsappSession.phone_e164 == phone,
                WhatsappSession.window_expires_at > datetime.now(tz=timezone.utc),
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return row is not None

    def _to_wa(self, phone: str) -> str:
        return phone if phone.startswith(_WA_PREFIX) else f"{_WA_PREFIX}{phone}"

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
        if kind is NotificationKind.FREEFORM:
            has_window = await self._has_open_window(to)
            if not has_window:
                logger.warning(
                    "FREEFORM send to %s denied — no open 24h window; degrading. idem=%s",
                    to[-4:],
                    idempotency_key,
                )
                return ProviderResult(sid=None, status="degraded", channel="whatsapp")
            # Free-form send inside the window
            try:
                msg = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._twilio_client.messages.create(
                        from_=self._from,
                        to=self._to_wa(to),
                        body=body or "",
                    ),
                )
                logger.info(
                    "FREEFORM sent to %s sid=%s idem=%s", to[-4:], msg.sid, idempotency_key
                )
                return ProviderResult(sid=msg.sid, status="sent", channel="whatsapp")
            except Exception as exc:
                logger.error(
                    "FREEFORM send failed to %s idem=%s err=%s", to[-4:], idempotency_key, exc
                )
                return ProviderResult(
                    sid=None, status="failed", channel="whatsapp", error=str(exc)
                )

        # TEMPLATE — window-independent
        try:
            create_kwargs: dict[str, Any] = dict(
                from_=self._from,
                to=self._to_wa(to),
            )
            # If template_name looks like a Twilio content SID (HX…) use content API;
            # otherwise encode as a body with substitutions for demo/test use.
            if template_name and template_name.startswith("HX"):
                create_kwargs["content_sid"] = template_name
                if variables:
                    create_kwargs["content_variables"] = json.dumps(variables)
            else:
                # Demo path: format variables inline into a text body
                rendered = template_name or ""
                if variables:
                    rendered += " " + " ".join(f"{k}={v}" for k, v in variables.items())
                create_kwargs["body"] = rendered

            msg = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self._twilio_client.messages.create(**create_kwargs),
            )
            logger.info(
                "TEMPLATE sent to %s template=%s sid=%s idem=%s",
                to[-4:],
                template_name,
                msg.sid,
                idempotency_key,
            )
            return ProviderResult(sid=msg.sid, status="sent", channel="whatsapp")
        except Exception as exc:
            logger.error(
                "TEMPLATE send failed to %s template=%s idem=%s err=%s",
                to[-4:],
                template_name,
                idempotency_key,
                exc,
            )
            return ProviderResult(
                sid=None, status="failed", channel="whatsapp", error=str(exc)
            )

    async def healthcheck(self) -> bool:
        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self._twilio_client.api.accounts(
                    self._twilio_client.account_sid
                ).fetch(),
            )
            return True
        except Exception:
            return False
