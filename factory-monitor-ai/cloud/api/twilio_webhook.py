# E:/Builds/factory_monitor/factory-monitor-ai/cloud/api/twilio_webhook.py
"""Inbound Twilio webhook: POST /webhooks/twilio/inbound.

Security: X-Twilio-Signature HMAC-SHA1 (not JWT).
Logic (§7):
  1. Validate signature.  Bad → 403.
  2. Parse From (E.164 after stripping 'whatsapp:' prefix) + Body.
  3. Match sender to the most-recent outbound messages row (direction='out',
     to_phone_e164 == from_phone, status='sent') → its incident.
  4. Matched:
     a. UPSERT whatsapp_sessions (window +24h).
     b. INSERT messages(direction='in').
     c. INSERT IncidentEvent(REPLY_RECEIVED).
     d. If body upper-stripped is 'ACK' → ACK incident (next_fire_at=NULL).
        If body upper-stripped is 'RESOLVED' → RESOLVED incident.
  5. Unmatched: INSERT unmatched_inbound (still open/refresh the WA window).
  6. Always return 200 with TwiML <Response/> (Twilio requires 200).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.api.deps import get_session_maker
from cloud.common.config import get_settings
from cloud.common.db.models import (
    Incident,
    IncidentEvent,
    IncidentStatus,
    Message,
    UnmatchedInbound,
    WhatsappSession,
)

webhook_router = APIRouter()

_ACK_KEYWORDS = {"ACK", "ACKNOWLEDGE", "NOTED"}
_RESOLVE_KEYWORDS = {"RESOLVED", "RESOLVE", "DONE", "FIXED", "CLOSED"}
_TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
_WINDOW_HOURS = 24


def _validate_twilio_signature(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """Verify Twilio's HMAC-SHA1 request signature."""
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    s = url + sorted_params
    expected = base64.b64encode(
        hmac.new(auth_token.encode(), s.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)


def _strip_whatsapp_prefix(phone: str) -> str:
    return phone.removeprefix("whatsapp:")


@webhook_router.post(
    "/webhooks/twilio/inbound",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def twilio_inbound(
    request: Request,
    session_maker: async_sessionmaker = Depends(get_session_maker),
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> PlainTextResponse:
    settings = get_settings()

    # Parse form body
    form_data = await request.form()
    params: dict[str, str] = {k: str(v) for k, v in form_data.items()}

    # Build the canonical URL Twilio signed (scheme+host+path, no query)
    url = str(request.url).split("?")[0]

    # Signature validation — fail-closed by default.
    # Only bypass when TWILIO_SKIP_SIGNATURE_CHECK=true (deliberate dev/test opt-in).
    if not settings.twilio_skip_signature_check:
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        if not auth_token or not x_twilio_signature or not _validate_twilio_signature(
            auth_token, url, params, x_twilio_signature
        ):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    raw_from = params.get("From", "")
    from_phone = _strip_whatsapp_prefix(raw_from)
    body_text = params.get("Body", "").strip()
    provider_sid = params.get("MessageSid")
    keyword = body_text.upper()

    now = datetime.now(tz=timezone.utc)
    window_expires = now + timedelta(hours=_WINDOW_HOURS)

    async with session_maker() as session:
        # Find the most-recent outbound messages row sent TO this phone
        # (direction='out', status='sent') — this is the canonical match table.
        outbound_msg: Message | None = (
            await session.execute(
                select(Message)
                .where(Message.to_phone_e164 == from_phone)
                .where(Message.direction == "out")
                .where(Message.status == "sent")
                .order_by(desc(Message.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

        # UPSERT whatsapp_sessions (open / refresh the 24h window)
        upsert_stmt = pg_insert(WhatsappSession).values(
            phone_e164=from_phone,
            window_expires_at=window_expires,
            last_inbound_at=now,
        ).on_conflict_do_update(
            index_elements=["phone_e164"],
            set_={
                "window_expires_at": window_expires,
                "last_inbound_at": now,
            },
        )
        await session.execute(upsert_stmt)

        if outbound_msg is not None:
            incident_id = outbound_msg.incident_id

            # Load the incident
            inc: Incident | None = (
                await session.execute(
                    select(Incident).where(Incident.id == incident_id)
                )
            ).scalar_one_or_none()

            # Record inbound message only when we have a valid incident to link to.
            # Avoids a dangling FK if the referenced incident no longer exists.
            if inc is not None:
                session.add(Message(
                    id=uuid.uuid4(),
                    incident_id=incident_id,
                    direction="in",
                    channel="whatsapp",
                    from_phone_e164=from_phone,
                    body=body_text,
                    provider_sid=provider_sid,
                    status="received",
                ))

                # Audit REPLY_RECEIVED
                session.add(IncidentEvent(
                    incident_id=incident_id,
                    type="REPLY_RECEIVED",
                    from_state=inc.status.value if inc.status else None,
                    to_state=inc.status.value if inc.status else None,
                    tier=inc.current_tier,
                    payload={"body": body_text, "from_phone": from_phone},
                ))

                # Keyword-driven close
                _ACTIVE = {
                    IncidentStatus.AWAITING_OPERATOR,
                    IncidentStatus.TIER1,
                    IncidentStatus.TIER2,
                }
                if keyword in _ACK_KEYWORDS and inc.status in _ACTIVE:
                    prev = inc.status.value
                    inc.status = IncidentStatus.ACK
                    inc.next_fire_at = None
                    inc.deadline_at = None
                    inc.acked_at = now
                    inc.updated_at = now
                    session.add(IncidentEvent(
                        incident_id=incident_id,
                        type="ACK",
                        from_state=prev,
                        to_state="ACK",
                        tier=inc.current_tier,
                        payload={"source": "whatsapp_reply", "body": body_text},
                    ))

                elif keyword in _RESOLVE_KEYWORDS and inc.status in (_ACTIVE | {IncidentStatus.ACK}):
                    prev = inc.status.value
                    inc.status = IncidentStatus.RESOLVED
                    inc.next_fire_at = None
                    inc.deadline_at = None
                    inc.resolved_at = now
                    inc.updated_at = now
                    session.add(IncidentEvent(
                        incident_id=incident_id,
                        type="RESOLVED",
                        from_state=prev,
                        to_state="RESOLVED",
                        tier=inc.current_tier,
                        payload={"source": "whatsapp_reply", "body": body_text},
                    ))
        else:
            # No matching outbound message — store as unmatched
            session.add(UnmatchedInbound(
                id=uuid.uuid4(),
                from_phone_e164=from_phone,
                body=body_text,
                provider_sid=provider_sid,
            ))

        await session.commit()

    return PlainTextResponse(content=_TWIML_EMPTY, media_type="application/xml")
