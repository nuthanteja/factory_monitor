"""Integration tests: DbGaugeCollector + make_due_collector against real Postgres.

Covers:
  1. escalation_due_rows — COUNT against the production escalation backlog query.
  2. outbox_pending — COUNT against the production outbox PENDING/SENDING query.

Both tests use testcontainers-Postgres + alembic upgrade head (mirrors
test_migration_applies.py). A types.SimpleNamespace stands in for settings —
make_due_collector only reads .alembic_database_url.
"""
from __future__ import annotations

import types
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")

ESCALATION_DUE_SQL = (
    "SELECT count(*) FROM incidents WHERE status IN "
    "('AWAITING_OPERATOR','TIER1','TIER2') AND next_fire_at IS NOT NULL "
    "AND next_fire_at <= now() AND (claimed_until IS NULL OR claimed_until < now())"
)

OUTBOX_PENDING_SQL = (
    "SELECT count(*) FROM outbox WHERE status IN ('PENDING','SENDING')"
)


@pytest.fixture(scope="module")
def pg_sync_url():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        sync_url = pg.get_connection_url()
        cfg = Config()
        cfg.set_main_option("script_location", MIGRATIONS)
        cfg.set_main_option("sqlalchemy.url", sync_url)
        command.upgrade(cfg, "head")
        yield sync_url


def _make_settings(sync_url: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(alembic_database_url=sync_url)


def _seed_due_incident(engine: sa.Engine) -> uuid.UUID:
    """Insert one incident that is due for escalation (status AWAITING_OPERATOR,
    next_fire_at in the past, claimed_until NULL).
    """
    inc_id = uuid.uuid4()
    past = datetime.now(tz=UTC) - timedelta(seconds=5)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO incidents "
                "(id, site_id, camera_id, anomaly_type, rule_id, severity, dedup_key, "
                "status, current_tier, next_fire_at, is_synthetic, created_at, updated_at) "
                "VALUES (:id, 'plant-01', 'cam_01', 'ppe_no_hardhat', 'PPE_NO_HARDHAT', "
                "'high', :dk, 'AWAITING_OPERATOR', 0, :nfa, false, now(), now())"
            ),
            {"id": str(inc_id), "dk": f"dk-{inc_id}", "nfa": past},
        )
    return inc_id


def _seed_pending_outbox(engine: sa.Engine, incident_id: uuid.UUID) -> uuid.UUID:
    """Insert one outbox row in PENDING status."""
    ob_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO outbox "
                "(id, incident_id, tier, to_phone_e164, channel, kind, "
                "template_name, variables, idempotency_key, status, "
                "attempts, max_attempts, next_attempt_at, created_at) "
                "VALUES (:id, :inc, 0, '+10000000001', 'whatsapp', 'TEMPLATE', "
                "'alert_operator', '{\"zone\":\"weld_bay\"}', :idem, 'PENDING', "
                "0, 6, now(), now())"
            ),
            {"id": str(ob_id), "inc": str(incident_id), "idem": str(ob_id)},
        )
    return ob_id


# ── Test 1: escalation_due_rows ───────────────────────────────────────────────

@pytest.mark.integration
def test_escalation_due_rows_collector(pg_sync_url: str) -> None:
    from cloud.common.metrics import make_due_collector

    settings = _make_settings(pg_sync_url)
    coll = make_due_collector("escalation_due_rows_test", "h", ESCALATION_DUE_SQL, settings)

    # With no due incidents the gauge should be present and zero (or absent).
    samples_before = list(coll.collect())
    if samples_before:
        value_before = samples_before[0].samples[0].value
        assert value_before == 0.0

    # Seed one due incident.
    engine = sa.create_engine(pg_sync_url)
    _seed_due_incident(engine)
    engine.dispose()

    # Now the collector must yield exactly 1.0 (or more if other tests left rows).
    samples_after = list(coll.collect())
    assert samples_after, "collector yielded no sample after seeding a due incident"
    value_after = samples_after[0].samples[0].value
    assert value_after >= 1.0


# ── Test 2: outbox_pending ────────────────────────────────────────────────────

@pytest.mark.integration
def test_outbox_pending_collector(pg_sync_url: str) -> None:
    from cloud.common.metrics import make_due_collector

    settings = _make_settings(pg_sync_url)
    coll = make_due_collector("outbox_pending_test", "h", OUTBOX_PENDING_SQL, settings)

    # Baseline (may be non-zero if other tests left PENDING rows).
    samples_before = list(coll.collect())
    baseline = samples_before[0].samples[0].value if samples_before else 0.0

    # Seed one incident + one PENDING outbox row.
    engine = sa.create_engine(pg_sync_url)
    inc_id = _seed_due_incident(engine)
    _seed_pending_outbox(engine, inc_id)
    engine.dispose()

    samples_after = list(coll.collect())
    assert samples_after, "collector yielded no sample after seeding a PENDING outbox row"
    value_after = samples_after[0].samples[0].value
    assert value_after >= baseline + 1.0
