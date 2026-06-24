"""ProviderChain: ordered list of NotificationProvider with fall-through semantics.

build_provider_chain(settings) constructs the chain from NOTIFY_PROVIDER_CHAIN
(comma-separated: whatsapp, sms, console).  Default is 'console' — the whole
escalation flow is demoable with zero external credentials.

Fall-through rule: if a provider returns status='degraded' OR status='failed',
the next provider in the chain is tried.  The first 'sent' result wins.
If all providers exhaust, the last result is returned (never raises).
"""
from __future__ import annotations

import logging
from typing import Any

from cloud.notifications.provider import NotificationKind, ProviderResult

logger = logging.getLogger("factory_monitor.notifications.chain")


class ProviderChain:
    """Tries providers in order; falls through on degraded/failed."""

    def __init__(self, providers: list[Any]) -> None:
        self._providers = providers

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
        if not self._providers:
            raise ValueError("ProviderChain is empty — at least one provider required")

        last_result: ProviderResult | None = None
        for provider in self._providers:
            result = await provider.send(
                to,
                kind,
                template_name=template_name,
                variables=variables,
                body=body,
                idempotency_key=idempotency_key,
            )
            last_result = result
            if result.status == "sent":
                return result
            logger.warning(
                "provider %s returned status=%s for idem=%s; trying next",
                type(provider).__name__,
                result.status,
                idempotency_key,
            )

        assert last_result is not None
        return last_result


def build_provider_chain(settings: Any) -> list[Any]:
    """Construct the ordered list of providers from settings.notify_provider_chain.

    Raises ValueError for unknown provider names or missing credentials.
    """
    names = [n.strip().lower() for n in settings.notify_provider_chain.split(",") if n.strip()]

    if not names:
        raise ValueError(
            "notify_provider_chain is empty — specify at least one provider (e.g. 'console')"
        )

    providers: list[Any] = []

    for name in names:
        if name == "console":
            from cloud.notifications.console import ConsoleProvider
            providers.append(ConsoleProvider())

        elif name == "whatsapp":
            sid = settings.twilio_account_sid
            token = settings.twilio_auth_token
            from_num = settings.twilio_whatsapp_from
            if not sid or not token or not from_num:
                raise ValueError(
                    "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM "
                    "are required when notify_provider_chain includes 'whatsapp'"
                )
            from cloud.notifications.twilio_whatsapp import TwilioWhatsAppProvider
            providers.append(
                TwilioWhatsAppProvider(
                    account_sid=sid,
                    auth_token=token,
                    from_number=from_num,
                )
            )

        elif name == "sms":
            sid = settings.twilio_account_sid
            token = settings.twilio_auth_token
            from_num = settings.twilio_sms_from
            if not sid or not token or not from_num:
                raise ValueError(
                    "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_SMS_FROM "
                    "are required when notify_provider_chain includes 'sms'"
                )
            from cloud.notifications.twilio_sms import TwilioSmsProvider
            providers.append(
                TwilioSmsProvider(
                    account_sid=sid,
                    auth_token=token,
                    from_number=from_num,
                )
            )

        else:
            raise ValueError(
                f"Unknown provider '{name}' in notify_provider_chain. "
                "Valid values: whatsapp, sms, console"
            )

    return providers
