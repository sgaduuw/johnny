"""johnny-web: Flask + Jinja read UI over the fleet state.

`create_app()` is the application factory; gunicorn imports it as
`johnny.web:create_app()`. Routes live in `routes.py`. Templates in
`templates/`. Read-only — all writes go through johnny-api.

Engine is constructed lazily via `engine_factory` so tests can inject
their own; the default factory reads `DATABASE_URL` via Settings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from flask import Flask, g
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from johnny.config import get_settings
from johnny.persistence import make_engine

EngineFactory = Callable[[], Engine]


def _default_engine_factory() -> Engine:
    return make_engine(get_settings().database_url)


def create_app(engine_factory: EngineFactory | None = None) -> Flask:
    factory = engine_factory or _default_engine_factory
    app = Flask(__name__)

    @app.before_request
    def _open_session() -> None:
        g.session = Session(factory())

    @app.teardown_appcontext
    def _close_session(_exception: BaseException | None = None) -> None:
        session = g.pop("session", None)
        if session is not None:
            session.close()

    app.add_template_filter(_naturaltime, "naturaltime")
    app.add_template_filter(_uptime, "uptime")
    app.add_template_filter(_mem_gb, "mem_gb")

    from johnny.web.routes import register_routes

    register_routes(app)
    return app


def _naturaltime(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    seconds = int((datetime.now(timezone.utc) - dt).total_seconds())
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _uptime(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _mem_gb(mb: int | None) -> str:
    if mb is None:
        return "—"
    if mb < 1024:
        return f"{mb} MB"
    return f"{mb // 1024} GB"
