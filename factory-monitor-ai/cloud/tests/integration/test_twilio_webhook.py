# E:/Builds/factory_monitor/factory-monitor-ai/cloud/tests/integration/test_twilio_webhook.py
from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.api.deps import get_session_maker
from cloud.api.main import create_app
from cloud.common.config import Settings
from cloud.common.db.models import (
    Incident,
    IncidentEvent,
    IncidentStatus,
    Message,
    UnmatchedInbound,
    WhatsappSession,
)

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")
TWILIO_AUTH_TOKEN = "test_twilio_auth_token_32chars_ok"
WEBHOOK_URL = "http://test/webhooks/twilio/inbound"


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Compute the Twilio HMAC-SHA1 signature the same way Twilio does."""
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    s = url + sorted_params
    sig = hmac.new(auth_token.encode(), s.encode(), hashlib.sha1).digest()
    return base64.b64encode(sig).decode()


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_url(pg_container: PostgresContainer) -> str:
    sync_url = pg_container.get_connection_url()
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")
    return _async_url(sync_url)


@pytest_asyncio.fixture
async def maker(migrated_url: str):
    engine = create_async_engine(migrated_url, future=True)
    m = async_sessionmaker(engine, expire_on_commit=False)
    yield m
    await engine.dispose()


@pytest_asyncio.fixture
async def client(maker):
    import os
    os.environ["TWILIO_AUTH_TOKEN"] = TWILIO_AUTH_TOKEN
    app = create_app(Settings(ws_fanout_enabled=False))
    app.dependency_overrides[get_session_maker] = lambda: maker
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _make_incident(status: IncidentStatus = IncidentStatus.AWAITING_OPERATOR) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id="zone_weld_bay",
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        object_class="person",
        track_id="cam_01:5001",
        severity="high",
        dedup_key=f"cam_01|cam_01:5001|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}",
        status=status,
        current_tier=1,
        next_fire_at=datetime.now(tz=UTC) + timedelta(seconds=300),
        snapshot_url="",
        is_synthetic=False,
    )


def _make_outbound_message(incident_id: uuid.UUID, to_phone: str) -> Message:
    """Seed a sent outbound messages row — the table the webhook matches against."""
    return Message(
        id=uuid.uuid4(),
        incident_id=incident_id,
        direction="out",
        channel="whatsapp",
        to_phone_e164=to_phone,
        body="Alert: PPE violation detected.",
        status="sent",
    )


def _inbound_params(from_phone: str, body: str, provider_sid: str | None = None) -> dict[str, str]:
    return {
        "From": f"whatsapp:{from_phone}",
        "Body": body,
        "MessageSid": provider_sid or f"SM{uuid.uuid4().hex}",
        "To": "whatsapp:+14155238886",
    }


def _signed_headers(params: dict[str, str]) -> dict[str, str]:
    sig = _twilio_signature(TWILIO_AUTH_TOKEN, WEBHOOK_URL, params)
    return {
        "X-Twilio-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }


# ── matched inbound → REPLY_RECEIVED ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_matched_inbound_opens_whatsapp_session_and_writes_message(client, maker):
    """A reply from a known phone (matching an outbound messages row) records message + session."""
    from_phone = "+12025550101"
    inc = _make_incident()
    out_msg = _make_outbound_message(inc.id, from_phone)

    async with maker() as s:
        s.add(inc)
        s.add(out_msg)
        await s.commit()

    params = _inbound_params(from_phone, "Got it, heading over now.")
    resp = await client.post(
        "/webhooks/twilio/inbound",
        content=urlencode(params),
        headers=_signed_headers(params),
    )
    assert resp.status_code == 200

    async with maker() as s:
        # WhatsApp session opened
        session_row = (
            await s.execute(
                select(WhatsappSession).where(WhatsappSession.phone_e164 == from_phone)
            )
        ).scalar_one()
        assert session_row.window_expires_at > datetime.now(tz=UTC)

        # Message recorded direction='in'
        msg = (
            await s.execute(
                select(Message)
                .where(Message.from_phone_e164 == from_phone)
                .where(Message.direction == "in")
            )
        ).scalar_one()
        assert msg.incident_id == inc.id
        assert msg.body == "Got it, heading over now."

        # Audit REPLY_RECEIVED
        evt = (
            await s.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == inc.id)
                .where(IncidentEvent.type == "REPLY_RECEIVED")
            )
        ).scalar_one()
        assert evt is not None


@pytest.mark.asyncio
async def test_ack_keyword_closes_incident(client, maker):
    """Reply of 'ACK' closes the incident (next_fire_at=NULL, status=ACK)."""
    from_phone = "+12025550202"
    inc = _make_incident()
    out_msg = _make_outbound_message(inc.id, from_phone)

    async with maker() as s:
        s.add(inc)
        s.add(out_msg)
        await s.commit()

    params = _inbound_params(from_phone, "ACK")
    resp = await client.post(
        "/webhooks/twilio/inbound",
        content=urlencode(params),
        headers=_signed_headers(params),
    )
    assert resp.status_code == 200

    async with maker() as s:
        row = (await s.execute(select(Incident).where(Incident.id == inc.id))).scalar_one()
        assert row.status == IncidentStatus.ACK
        assert row.next_fire_at is None


@pytest.mark.asyncio
async def test_resolve_keyword_closes_incident(client, maker):
    """Reply of 'RESOLVED' closes the incident (status=RESOLVED)."""
    from_phone = "+12025550303"
    inc = _make_incident()
    out_msg = _make_outbound_message(inc.id, from_phone)

    async with maker() as s:
        s.add(inc)
        s.add(out_msg)
        await s.commit()

    params = _inbound_params(from_phone, "RESOLVED")
    resp = await client.post(
        "/webhooks/twilio/inbound",
        content=urlencode(params),
        headers=_signed_headers(params),
    )
    assert resp.status_code == 200

    async with maker() as s:
        row = (await s.execute(select(Incident).where(Incident.id == inc.id))).scalar_one()
        assert row.status == IncidentStatus.RESOLVED
        assert row.next_fire_at is None


# ── bad signature → 403, no DB write ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_bad_signature_returns_403(client, maker):
    bad_phone = "+12025550999"
    params = _inbound_params(bad_phone, "ACK")
    resp = await client.post(
        "/webhooks/twilio/inbound",
        content=urlencode(params),
        headers={
            "X-Twilio-Signature": "invalidsignature==",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert resp.status_code == 403

    # Confirm no DB row was written for this sender
    async with maker() as s:
        row = (
            await s.execute(
                select(UnmatchedInbound).where(UnmatchedInbound.from_phone_e164 == bad_phone)
            )
        ).scalars().first()
        assert row is None, "No unmatched_inbound row should be written on bad signature"


# ── missing auth token → 403 (fail-closed) ───────────────────────────────────

@pytest.mark.asyncio
async def test_no_auth_token_fails_closed(maker):
    """When TWILIO_SKIP_SIGNATURE_CHECK is False (default) and no auth token is
    configured, requests must be rejected with 403 — no silent bypass."""
    import os
    # Ensure no token and skip flag not set
    env_without_token = {k: v for k, v in os.environ.items() if k != "TWILIO_AUTH_TOKEN"}
    with patch.dict(os.environ, env_without_token, clear=True):
        from cloud.common.config import Settings, get_settings
        get_settings.cache_clear()
        app = create_app(Settings(ws_fanout_enabled=False))
        # Override session maker so the app can boot; request should 403 before DB
        engine = create_async_engine(
            # Use a dummy URL — should never reach DB on 403 path
            "postgresql+asyncpg://factory:factory@localhost:5432/factory",
            future=True,
        )
        m = async_sessionmaker(engine, expire_on_commit=False)
        app.dependency_overrides[get_session_maker] = lambda: m
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            params = _inbound_params("+10005550000", "hello")
            resp = await c.post(
                "/webhooks/twilio/inbound",
                content=urlencode(params),
                headers={
                    "X-Twilio-Signature": "anysignature==",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        await engine.dispose()
        get_settings.cache_clear()
    assert resp.status_code == 403, f"Expected 403 when no auth token; got {resp.status_code}"


# ── unmatched inbound ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unmatched_inbound_is_stored(client, maker):
    """A reply from an unknown phone (no outbound messages row) goes to unmatched_inbound."""
    unknown_phone = "+19995550000"
    params = _inbound_params(unknown_phone, "Hello?")
    resp = await client.post(
        "/webhooks/twilio/inbound",
        content=urlencode(params),
        headers=_signed_headers(params),
    )
    assert resp.status_code == 200

    async with maker() as s:
        row = (
            await s.execute(
                select(UnmatchedInbound)
                .where(UnmatchedInbound.from_phone_e164 == unknown_phone)
                .order_by(UnmatchedInbound.created_at.desc())
            )
        ).scalars().first()
        assert row is not None
        assert row.body == "Hello?"
