"""Entrypoint: python -m cloud.escalation_worker"""
from __future__ import annotations

import asyncio
import logging
import signal

from cloud.common.config import get_settings
from cloud.common.db.session import session_factory
from cloud.common.redis_client import get_redis
from cloud.common.ws_publisher import publish_incident_event
from cloud.escalation_worker.worker import EscalationWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    maker = session_factory(settings)

    redis_client = get_redis(settings)
    worker = EscalationWorker(
        session_maker=maker,
        poll_interval_seconds=1.0,
        lease_seconds=settings.escalation_lease_seconds,
        batch=10,
        publisher=lambda ch: publish_incident_event(
            redis_client, settings.ws_redis_channel, ch
        ),
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    await worker.start()
    run_task = asyncio.create_task(worker.run_until_stopped())

    await stop_event.wait()
    await worker.stop()
    await asyncio.wait_for(run_task, timeout=10.0)
    logger.info("escalation worker exited cleanly")


if __name__ == "__main__":
    asyncio.run(main())
