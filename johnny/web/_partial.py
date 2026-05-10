"""Pick template name based on whether this is an HTMX swap request.

`htmx.org` sets `HX-Request: true` on every request it issues. Routes
use that signal to render just the table fragment when the request
came from an in-page swap, and the full page otherwise. Same URL,
two render paths.
"""

from __future__ import annotations

from flask import request


def is_htmx_request() -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def pick(full: str, partial: str) -> str:
    """Return `partial` when the current request is an HTMX swap,
    else `full`. Convenience used by list routes."""
    return partial if is_htmx_request() else full
