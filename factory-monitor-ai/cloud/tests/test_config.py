import importlib

import cloud.common.config as config_mod


def _fresh_settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(config_mod)
    config_mod.get_settings.cache_clear()
    return config_mod.get_settings()


def test_defaults(monkeypatch):
    monkeypatch.delenv("OPERATOR_GRACE_SECONDS", raising=False)
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert s.operator_grace_seconds == 120
    assert s.kafka_anomalies_topic == "vision.anomalies.v1"
    assert s.kafka_dlq_topic == "vision.anomalies.dlq"
    assert s.kafka_consumer_group == "ingest-worker"
    assert s.kafka_bootstrap_servers == "localhost:9092"


def test_env_override(monkeypatch):
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="kafka:9092",
        REDIS_URL="redis://localhost:6379/0",
        OPERATOR_GRACE_SECONDS="45",
    )
    assert s.operator_grace_seconds == 45
    assert s.kafka_bootstrap_servers == "kafka:9092"


def test_get_settings_is_cached(monkeypatch):
    _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert config_mod.get_settings() is config_mod.get_settings()


def test_escalation_lease_seconds_default(monkeypatch):
    monkeypatch.delenv("ESCALATION_LEASE_SECONDS", raising=False)
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert s.escalation_lease_seconds == 30


def test_escalation_lease_seconds_env_override(monkeypatch):
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        REDIS_URL="redis://localhost:6379/0",
        ESCALATION_LEASE_SECONDS="77",
    )
    assert s.escalation_lease_seconds == 77


def test_ws_fanout_settings_have_defaults_and_env_override(monkeypatch):
    from cloud.common.config import Settings

    s = Settings()
    assert s.ws_redis_channel == "dashboard:incidents"
    assert s.ws_fallback_poll_seconds == 1.0
    assert s.ws_fallback_batch == 200

    monkeypatch.setenv("WS_REDIS_CHANNEL", "dashboard:incidents:test")
    monkeypatch.setenv("WS_FALLBACK_POLL_SECONDS", "0.25")
    monkeypatch.setenv("WS_FALLBACK_BATCH", "50")
    s2 = Settings()
    assert s2.ws_redis_channel == "dashboard:incidents:test"
    assert s2.ws_fallback_poll_seconds == 0.25
    assert s2.ws_fallback_batch == 50


def test_ws_channel_default(monkeypatch):
    monkeypatch.delenv("WS_CHANNEL", raising=False)
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert s.ws_channel == "dashboard:incidents"
    assert s.ws_poll_interval_seconds == 2.0


def test_ws_env_override(monkeypatch):
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        REDIS_URL="redis://localhost:6379/0",
        WS_CHANNEL="dashboard:custom",
        WS_POLL_INTERVAL_SECONDS="0.5",
    )
    assert s.ws_channel == "dashboard:custom"
    assert s.ws_poll_interval_seconds == 0.5
