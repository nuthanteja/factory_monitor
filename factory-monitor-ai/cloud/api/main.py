from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from cloud.api.deps import get_session_maker
from cloud.api.routes import router
from cloud.api.twilio_webhook import webhook_router
from cloud.api.ws import ws_router
from cloud.common.config import Settings
from cloud.common.db.session import session_factory
from cloud.common.metrics import metrics_response
from cloud.common.redis_client import close_redis, get_redis
from cloud.common.seed_cameras import seed_cameras
from cloud.common.ws.detection_hub import DetectionHub
from cloud.common.ws.fanout import start_ws_fanout, stop_ws_fanout
from cloud.common.ws.manager import ConnectionManager


def create_app(settings: Settings | None = None) -> FastAPI:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    from cloud.common.logging_json import setup_json_logging
    from cloud.common.telemetry import setup_telemetry

    _settings = settings or Settings()

    setup_json_logging()
    setup_telemetry(
        _settings.otel_service_name or "api",
        endpoint=_settings.otel_exporter_otlp_endpoint,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN001
        # --- startup ---
        if _settings.ws_fanout_enabled:
            app.state.ws_redis = get_redis(_settings)
            await start_ws_fanout(app)
        if _settings.detections_ws_enabled:
            # Reuse the same redis client as the fanout (or create one if fanout is off).
            redis = getattr(app.state, "ws_redis", None) or get_redis(_settings)
            app.state.detection_hub = DetectionHub(redis)
        if _settings.seed_cameras_enabled:
            maker = getattr(app.state, "ws_session_maker", None)
            if maker is not None:
                await seed_cameras(maker)
        # --- yield to serve requests ---
        yield
        # --- shutdown ---
        if _settings.detections_ws_enabled:
            hub: DetectionHub | None = getattr(app.state, "detection_hub", None)
            if hub is not None:
                await hub.close()
        if _settings.ws_fanout_enabled:
            await stop_ws_fanout(app)
            await close_redis()

    app = FastAPI(title="Factory Monitor API", version="1.0.0", lifespan=lifespan)
    FastAPIInstrumentor.instrument_app(app)
    app.include_router(router)
    app.include_router(webhook_router)
    app.include_router(ws_router)

    @app.get("/metrics")
    def _metrics() -> Response:  # noqa: ANN202
        body, content_type = metrics_response()
        return Response(content=body, media_type=content_type)

    # One process-wide WS hub; slice ws-redis reaches it via app.state.ws_manager.
    app.state.ws_manager = ConnectionManager()
    app.state.settings = _settings

    if settings is not None:
        maker = session_factory(settings)
        app.dependency_overrides[get_session_maker] = lambda: maker
        app.state.ws_session_maker = maker

    return app


# ASGI entrypoint for uvicorn: `uvicorn cloud.api.main:app`
app = create_app(Settings())
