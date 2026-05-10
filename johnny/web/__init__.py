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
    app.add_template_filter(_play_stats, "play_stats")

    @app.context_processor
    def _inject_settings() -> dict[str, object]:
        return {"settings": get_settings()}

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


def _play_stats(stats: dict[str, dict[str, int]] | None) -> dict[str, int] | None:
    """Aggregate per-host task counts in a playbook's stats dict.

    `Playbook.stats` is keyed by host fqdn; each value is the task
    outcome counts for that host (ok/changed/failed/unreachable/etc.)
    over the play. Sum across hosts so the list view can show a
    one-line summary; expose `hosts` separately so the template can
    render run size up front.

    Returns None when stats is None (the play hasn't reported finish).
    """
    if not stats:
        return None
    keys = ("ok", "changed", "failed", "unreachable", "skipped", "rescued", "ignored")
    totals: dict[str, int] = {k: 0 for k in keys}
    for host_counts in stats.values():
        for k in keys:
            totals[k] += host_counts.get(k, 0) or 0
    totals["hosts"] = len(stats)
    return totals
