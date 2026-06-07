"""johnny — Ansible callback receiver and fleet-state browser.

The v1 Flask app entrypoint that previously lived here has been
disabled while v2 (Flask-web + FastAPI-api + service classes,
documented in CONTEXT.md) is built up alongside. The v1 modules
under johnny/routes/, johnny/models/, johnny/tasks/, etc. are
preserved on disk as reference but no longer auto-wired.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

# Runtime version, read from the installed package metadata (uv /
# pip install). The release flow bumps `version` in pyproject.toml on
# the release branch alongside the git tag. The sentinel covers a
# source-tree-only checkout where `johnny` isn't installed; in that
# mode the footer shows "0.0.0+unknown" instead of crashing.
try:
    __version__ = _pkg_version("johnny")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
