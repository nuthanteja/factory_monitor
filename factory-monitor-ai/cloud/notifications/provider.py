"""NotificationProvider Protocol + shared types.

Design decision (§7):
  send(kind=TEMPLATE)  → always works (window-independent, pre-approved template).
  send(kind=FREEFORM)  → only allowed inside an open 24h whatsapp_sessions window;
                          providers must enforce this policy themselves or degrade.

The Protocol is @runtime_checkable so the chain factory can isinstance()-guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class NotificationKind(str, Enum):
    TEMPLATE = "TEMPLATE"
    FREEFORM = "FREEFORM"


@dataclass(frozen=True)
class ProviderResult:
    sid: str | None        # provider message SID / reference; None for console
    status: str            # "sent" | "degraded" | "failed"
    channel: str           # "whatsapp" | "sms" | "console"
    error: str | None = None   # failure detail; None on success


@runtime_checkable
class NotificationProvider(Protocol):
    """Structural interface for all notification back-ends.

    Implementers must NOT inherit this class — structural subtyping only.
    The caller is responsible for choosing TEMPLATE vs FREEFORM; providers that
    cannot honour the requested kind must raise or degrade as documented.
    """

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
        """Send a notification. Must be idempotent on idempotency_key."""
        ...

    async def healthcheck(self) -> bool:
        """Return True iff the provider is operational."""
        ...
