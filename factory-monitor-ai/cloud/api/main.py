from __future__ import annotations

from fastapi import FastAPI

from cloud.api.deps import get_session_maker
from cloud.api.routes import router
from cloud.common.config import Settings
from cloud.common.db.session import session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Factory Monitor API", version="1.0.0")
    app.include_router(router)

    if settings is not None:
        maker = session_factory(settings)
        app.dependency_overrides[get_session_maker] = lambda: maker

    return app


# ASGI entrypoint for uvicorn: `uvicorn cloud.api.main:app`
app = create_app(Settings())
