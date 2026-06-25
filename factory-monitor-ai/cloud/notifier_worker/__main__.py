"""Entry point: python -m cloud.notifier_worker"""
from __future__ import annotations

import asyncio
import logging

from cloud.common.config import get_settings
from cloud.common.db.session import session_factory
from cloud.notifications.chain import ProviderChain, build_provider_chain
from cloud.notifier_worker.relay import run_forever

logger = logging.getLogger("factory_monitor.notifier_worker")


async def main() -> None:
    from cloud.common.logging_json import setup_json_logging
    from cloud.common.telemetry import setup_telemetry

    setup_json_logging()
    settings = get_settings()
    setup_telemetry(
        settings.otel_service_name or "notifier_worker",
        endpoint=settings.otel_exporter_otlp_endpoint,
    )
    from cloud.common.metrics import _register_once, make_due_collector, start_metrics_server
    start_metrics_server(settings.notifier_metrics_port)
    _register_once(make_due_collector(
        "outbox_pending", "Outbox rows awaiting delivery (PENDING or SENDING).",
        "SELECT count(*) FROM outbox WHERE status IN ('PENDING','SENDING')", settings,
    ))
    maker = session_factory(settings)
    chain = ProviderChain(build_provider_chain(settings))
    logger.info(
        "notifier-worker starting provider_chain=%s", settings.notify_provider_chain
    )
    await run_forever(maker, chain)


if __name__ == "__main__":
    asyncio.run(main())
