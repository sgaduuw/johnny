"""johnny-api: FastAPI app receiving callback POSTs from the plugin.

`create_app()` is the application factory; uvicorn's import string
points at it (`uvicorn johnny.api:create_app --factory`). Route
handlers live in `routes.py`, dependency wiring in `deps.py`.
"""

from __future__ import annotations

from fastapi import FastAPI

from johnny.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="johnny-api",
        version="0.1.0",
        description="Ansible callback receiver. See johnny/contracts/v1.py.",
    )
    app.include_router(router)
    return app
