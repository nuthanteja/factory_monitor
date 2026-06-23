"""CLI: consume one AnomalyEvent from vision.anomalies.v1 and print it."""
from __future__ import annotations

import asyncio
import json as _json

from cloud.common.config import get_settings
from cloud.common.kafka import deserialize_event, make_consumer


async def consume_one(timeout: float = 30.0) -> dict:
    settings = get_settings()
    consumer = await make_consumer(
        settings.kafka_anomalies_topic,
        group_id="phase0-consume-print",
        auto_offset_reset="earliest",
    )
    try:
        msg = await asyncio.wait_for(consumer.getone(), timeout=timeout)
        event = deserialize_event(msg.value)
        printable = event.model_dump(mode="json")
        print(
            f"consumed key={msg.key.decode() if msg.key else None} "
            f"partition={msg.partition} offset={msg.offset}"
        )
        print(_json.dumps(printable, indent=2))
        return printable
    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(consume_one())
