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


def test_ws_redis_channel_default(monkeypatch):
    monkeypatch.delenv("WS_REDIS_CHANNEL", raising=False)
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        REDIS_URL="redis://localhost:6379/0",
    )
    assert s.ws_redis_channel == "dashboard:incidents"
    assert s.ws_fallback_poll_seconds == 1.0
    assert s.ws_fallback_batch == 200
    assert not hasattr(s, "ws_channel"), "stale duplicate ws_channel must not exist"


def test_ws_redis_env_override(monkeypatch):
    s = _fresh_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
        ALEMBIC_DATABASE_URL="postgresql://u:p@localhost:5432/db",
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        REDIS_URL="redis://localhost:6379/0",
        WS_REDIS_CHANNEL="dashboard:custom",
        WS_FALLBACK_POLL_SECONDS="0.5",
        WS_FALLBACK_BATCH="100",
    )
    assert s.ws_redis_channel == "dashboard:custom"
    assert s.ws_fallback_poll_seconds == 0.5
    assert s.ws_fallback_batch == 100


def test_otel_settings_default_none_and_read_env(monkeypatch):
    from cloud.common.config import Settings
    s = Settings()
    assert s.otel_exporter_otlp_endpoint is None
    assert s.otel_service_name is None
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "ingest_worker")
    s2 = Settings()
    assert s2.otel_exporter_otlp_endpoint == "http://otel-collector:4318"
    assert s2.otel_service_name == "ingest_worker"


def test_metrics_port_settings(monkeypatch):
    from cloud.common.config import Settings
    s = Settings()
    assert s.ingest_metrics_port == 9101
    assert s.escalation_metrics_port == 9102
    assert s.notifier_metrics_port == 9103
    assert s.edge_metrics_port == 9108
    monkeypatch.setenv("ESCALATION_METRICS_PORT", "9999")
    assert Settings().escalation_metrics_port == 9999
