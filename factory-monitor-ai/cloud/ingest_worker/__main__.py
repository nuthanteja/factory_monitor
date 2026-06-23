from __future__ import annotations

import asyncio
import logging

from cloud.common.config import Settings
from cloud.ingest_worker.consumer import IngestConsumer


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    consumer = IngestConsumer(settings)
    await consumer.start()
    try:
        await consumer.run_forever()
    finally:
        await consumer.stop()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
