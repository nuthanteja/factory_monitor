from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "cloud" / "migrations"

EXPECTED_TABLES = {
    "incidents", "incident_events", "escalation_idempotency", "outbox",
    "messages", "whatsapp_sessions", "unmatched_inbound", "users",
    "on_call_assignments", "escalation_tiers", "zones", "cameras",
    "density_snapshots",
}


@pytest.fixture(scope="module")
def pg_url():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg.get_connection_url()


def _alembic_config(sync_url: str) -> Config:
    cfg = Config(str(MIGRATIONS / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


def test_upgrade_creates_full_schema(pg_url):
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "head")

    engine = sa.create_engine(pg_url)
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    assert EXPECTED_TABLES <= tables, f"missing: {EXPECTED_TABLES - tables}"

    inc_indexes = {ix["name"] for ix in insp.get_indexes("incidents")}
    assert "uq_incident_open_dedup" in inc_indexes
    assert "idx_incident_due" in inc_indexes

    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = 'incident_status'")
        ).first()
        assert row is not None
    engine.dispose()


def test_downgrade_then_upgrade_roundtrip(pg_url):
    cfg = _alembic_config(pg_url)
    command.downgrade(cfg, "base")
    engine = sa.create_engine(pg_url)
    insp = sa.inspect(engine)
    assert "incidents" not in set(insp.get_table_names())
    engine.dispose()
    command.upgrade(cfg, "head")
