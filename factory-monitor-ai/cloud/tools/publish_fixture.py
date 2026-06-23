"""CLI: publish the canonical fixture AnomalyEvent to vision.anomalies.v1."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cloud.common.config import get_settings
from cloud.common.kafka import make_producer, publish_event
from cloud.common.schemas.anomaly import AnomalyEvent

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "shared" / "contracts" / "anomaly_event.example.json"


def load_fixture_event() -> AnomalyEvent:
    return AnomalyEvent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


async def main() -> None:
    settings = get_settings()
    event = load_fixture_event()
    producer = await make_producer()
    try:
        await publish_event(producer, settings.kafka_anomalies_topic, event)
        await producer.flush()
        print(f"published event_id={event.event_id} -> {settings.kafka_anomalies_topic}")
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())
