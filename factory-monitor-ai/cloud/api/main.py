from __future__ import annotations

from fastapi import FastAPI

from cloud.api.deps import get_session_maker
from cloud.api.routes import router
from cloud.api.twilio_webhook import webhook_router
from cloud.api.ws import ws_router
from cloud.common.config import Settings
from cloud.common.db.session import session_factory
from cloud.common.ws.manager import ConnectionManager


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Factory Monitor API", version="1.0.0")
    app.include_router(router)
    app.include_router(webhook_router)
    app.include_router(ws_router)

    # One process-wide WS hub; slice ws-redis reaches it via app.state.ws_manager.
    app.state.ws_manager = ConnectionManager()

    if settings is not None:
        maker = session_factory(settings)
        app.dependency_overrides[get_session_maker] = lambda: maker
        app.state.ws_session_maker = maker

    return app


# ASGI entrypoint for uvicorn: `uvicorn cloud.api.main:app`
app = create_app(Settings())
