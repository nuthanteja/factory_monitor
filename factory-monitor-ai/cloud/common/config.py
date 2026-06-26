"""Central application settings, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Postgres
    database_url: str = "postgresql+asyncpg://factory:factory@localhost:5432/factory"
    alembic_database_url: str = "postgresql://factory:factory@localhost:5432/factory"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_anomalies_topic: str = "vision.anomalies.v1"
    kafka_dlq_topic: str = "vision.anomalies.dlq"
    kafka_consumer_group: str = "ingest-worker"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # WebSocket live fan-out (Phase 2b)
    ws_redis_channel: str = "dashboard:incidents"
    ws_fallback_poll_seconds: float = 1.0  # Postgres-poll fallback cadence when Redis is down
    ws_fallback_batch: int = 200           # max incidents re-broadcast per fallback poll
    ws_fanout_enabled: bool = True         # gate: set False in tests to skip Redis/fanout startup

    # Camera/zone seed (Phase 4a)
    seed_cameras_enabled: bool = True      # gate: set False in tests to skip seeding at startup

    # Detection overlay emit (default-off; edge only)
    emit_detections: bool = False          # gate: set True to stream per-frame boxes to Redis
    detection_max_fps: float = 10.0        # max publish rate (Hz) for detection overlay

    # Detection WebSocket relay (cloud)
    detections_ws_enabled: bool = True     # gate: set False in tests to skip Redis/hub startup

    # Heatmap density emit (Phase 4b)
    emit_heatmap: bool = False
    heatmap_min_interval_s: float = 5.0
    kafka_heatmap_topic: str = "vision.heatmap.v1"
    kafka_heatmap_group: str = "heatmap-worker"
    heatmap_redis_channel: str = "dashboard:heatmap"
    heatmap_metrics_port: int = 9104

    # Escalation timing
    operator_grace_seconds: int = 120
    escalation_lease_seconds: int = 30

    # Notification provider chain
    notify_provider_chain: str = "console"  # comma-separated: whatsapp,sms,console

    # Twilio credentials (optional — required when chain includes whatsapp or sms)
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_whatsapp_from: str | None = None  # E.164, e.g. +14155238886
    twilio_sms_from: str | None = None       # E.164

    # Twilio
    # TWILIO_SKIP_SIGNATURE_CHECK — deliberate dev/test opt-in only
    twilio_skip_signature_check: bool = False

    # --- Observability (Phase 3b) ---
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str | None = None
    ingest_metrics_port: int = 9101
    escalation_metrics_port: int = 9102
    notifier_metrics_port: int = 9103
    edge_metrics_port: int = 9108


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
