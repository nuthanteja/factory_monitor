from __future__ import annotations

import asyncio

from cloud.common.config import Settings
from cloud.heatmap_worker.consumer import HeatmapConsumer


async def _amain() -> None:
    from cloud.common.logging_json import setup_json_logging
    from cloud.common.telemetry import setup_telemetry

    setup_json_logging()
    settings = Settings()
    setup_telemetry(
        settings.otel_service_name or "heatmap_worker",
        endpoint=settings.otel_exporter_otlp_endpoint,
    )
    from cloud.common.metrics import start_metrics_server

    start_metrics_server(settings.heatmap_metrics_port)
    consumer = HeatmapConsumer(settings)
    await consumer.start()
    try:
        await consumer.run_forever()
    finally:
        await consumer.stop()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
