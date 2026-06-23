"""Entry point: python -m cloud.notifier_worker"""
from __future__ import annotations

import asyncio
import logging

from cloud.common.config import get_settings
from cloud.common.db.session import session_factory
from cloud.notifications.chain import build_provider_chain, ProviderChain
from cloud.notifier_worker.relay import run_forever

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("factory_monitor.notifier_worker")


async def main() -> None:
    settings = get_settings()
    maker = session_factory(settings)
    chain = ProviderChain(build_provider_chain(settings))
    logger.info(
        "notifier-worker starting provider_chain=%s", settings.notify_provider_chain
    )
    await run_forever(maker, chain)


if __name__ == "__main__":
    asyncio.run(main())
