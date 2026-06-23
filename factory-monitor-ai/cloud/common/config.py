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
    twilio_skip_signature_check: bool = False  # TWILIO_SKIP_SIGNATURE_CHECK — deliberate dev/test opt-in only


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
