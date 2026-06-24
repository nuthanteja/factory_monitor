"""Fan-out supervisor: Redis-primary with auto-failover to the Postgres poll.

Redis pub/sub is the low-latency primary; if Redis is unreachable the
supervisor transparently runs the Postgres-poll fallback (design §8 'Redis
down'). It re-checks Redis health each supervision cycle, so recovery switches
back to the push path without a restart. This is the single coroutine slice-1's
app lifespan starts/stops.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.config import Settings
from cloud.common.ws.fallback import PostgresPollFallback
from cloud.common.ws.subscriber import RedisFanoutSubscriber

logger = logging.getLogger(__name__)


class FanoutSupervisor:
    def __init__(
        self,
        redis_client: object,
        session_maker: async_sessionmaker,
        manager: object,
        settings: Settings,
    ) -> None:
        self._redis = redis_client
        self._session_maker = session_maker
        self._manager = manager
        self._settings = settings

    async def _redis_healthy(self) -> bool:
        try:
            await self._redis.ping()
            return True
        except Exception:  # noqa: BLE001 — any failure => treat Redis as down
            return False

    async def _run_fallback_for_interval(self, stop_event: asyncio.Event) -> None:
        """Run the poll fallback until stop OR until a short supervision window
        elapses (then we loop and re-check Redis health)."""
        fallback = PostgresPollFallback(
            self._session_maker,
            self._manager,
            poll_seconds=self._settings.ws_fallback_poll_seconds,
            batch=self._settings.ws_fallback_batch,
        )
        local_stop = asyncio.Event()
        task = asyncio.create_task(fallback.run(stop_event=local_stop))
        # supervision window: a few poll cycles before re-probing Redis
        window = max(self._settings.ws_fallback_poll_seconds * 5, 0.5)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=window)
        except TimeoutError:
            pass
        finally:
            local_stop.set()
            await asyncio.wait_for(task, timeout=5)

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        stop_event = stop_event or asyncio.Event()
        logger.info("ws fanout supervisor starting (redis-primary, poll-fallback)")
        while not stop_event.is_set():
            if await self._redis_healthy():
                logger.info("ws fanout: redis healthy — starting subscriber (primary)")
                subscriber = RedisFanoutSubscriber(
                    self._redis,
                    self._session_maker,
                    self._manager,
                    channel=self._settings.ws_redis_channel,
                )
                try:
                    await subscriber.run(stop_event=stop_event)
                except Exception:  # noqa: BLE001 — subscriber died; fall back
                    logger.exception("ws redis subscriber failed — failing over to poll")
            else:
                logger.warning("redis unhealthy — ws fan-out using postgres poll fallback")
                await self._run_fallback_for_interval(stop_event)
        logger.info("ws fanout supervisor stopped")


async def start_ws_fanout(app: object) -> None:
    """Start the fan-out supervisor as a background task; store at app.state.ws_fanout.

    Reads app.state.ws_redis, app.state.ws_session_maker, app.state.ws_manager.
    If app.state.ws_redis is absent, runs fallback-only (redis_client with a
    ping that always raises ConnectionError).
    """
    redis_client = getattr(app.state, "ws_redis", None)  # type: ignore[attr-defined]
    session_maker = app.state.ws_session_maker  # type: ignore[attr-defined]
    manager = app.state.ws_manager  # type: ignore[attr-defined]

    if redis_client is None:
        # No Redis configured — use a permanent-down stub so supervisor runs fallback-only.
        class _NoRedis:
            async def ping(self) -> bool:
                raise ConnectionError("ws_redis not configured")

        redis_client = _NoRedis()

    from cloud.common.config import get_settings

    settings = getattr(app.state, "settings", None) or get_settings()  # type: ignore[attr-defined]
    supervisor = FanoutSupervisor(redis_client, session_maker, manager, settings)
    stop_event = asyncio.Event()
    task = asyncio.create_task(supervisor.run(stop_event=stop_event))
    app.state.ws_fanout = task  # type: ignore[attr-defined]
    app.state.ws_fanout_stop = stop_event  # type: ignore[attr-defined]
    logger.info("ws fanout supervisor background task started")


async def stop_ws_fanout(app: object) -> None:
    """Signal stop_event then cancel and await the fan-out supervisor task.

    Sets ws_fanout_stop first so the supervisor's inner loops drain cleanly,
    then cancels the task and awaits it WITHOUT asyncio.shield so a slow
    teardown never leaks the task past the timeout.
    """
    task: asyncio.Task | None = getattr(app.state, "ws_fanout", None)  # type: ignore[attr-defined]
    if task is None or task.done():
        return
    if (ev := getattr(app.state, "ws_fanout_stop", None)) is not None:
        ev.set()
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=5)  # no shield — task must terminate
    except (TimeoutError, asyncio.CancelledError):
        pass
    logger.info("ws fanout supervisor stopped")
