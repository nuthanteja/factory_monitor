"""ConsoleProvider — the always-available default notification back-end.

No external credentials required. Every send() logs to the structured logger
and returns status='sent'. FREEFORM and TEMPLATE are both accepted because
there is no channel policy to enforce. This makes the entire escalation flow
demoable with zero external setup.
"""
from __future__ import annotations

import logging

from cloud.notifications.provider import NotificationKind, ProviderResult

logger = logging.getLogger("factory_monitor.notifications.console")


class ConsoleProvider:
    """Structural implementation of NotificationProvider (no explicit inherit needed)."""

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
        if kind is NotificationKind.TEMPLATE:
            logger.info(
                "[CONSOLE] SEND to=%s kind=TEMPLATE template=%s variables=%r idem=%s",
                to,
                template_name,
                variables,
                idempotency_key,
            )
        else:
            logger.info(
                "[CONSOLE] SEND to=%s kind=FREEFORM body=%r idem=%s",
                to,
                body,
                idempotency_key,
            )
        return ProviderResult(sid=None, status="sent", channel="console")

    async def healthcheck(self) -> bool:
        return True
