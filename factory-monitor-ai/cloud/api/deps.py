from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker


def get_session_maker() -> async_sessionmaker:
    """Overridden in tests and replaced by app config in create_app().

    Raises if used without configuration so misuse fails loudly.
    """
    raise RuntimeError("get_session_maker dependency not configured")
