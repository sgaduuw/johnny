"""Tests for johnny.config.Settings.

Both the API tier and the web tier read settings via the same
get_settings() (lru_cache'd). The truncate-chars knob is only
consumed by web today, via a context processor that injects
`settings` into every template — locking the wiring at render
time means a future operator's `JOHNNY_TRUNCATE_CHARS=25` actually
reaches the browser.
"""

from __future__ import annotations

import importlib

import pytest
from flask.testing import FlaskClient
from sqlalchemy.engine import Engine

import johnny
from johnny.config import Settings, get_settings
from johnny.web import create_app


class TestTruncateCharsSetting:
    def test_default_is_15(self) -> None:
        # `_env_file=None` bypasses any .env in the working dir so this
        # test is repeatable independent of dev-machine setup.
        s = Settings(_env_file=None)
        assert s.johnny_truncate_chars == 15

    def test_env_override_is_picked_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("JOHNNY_TRUNCATE_CHARS", "25")
        s = Settings(_env_file=None)
        assert s.johnny_truncate_chars == 25

    def test_env_var_is_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # case_sensitive=False on the model — operator typing
        # `johnny_truncate_chars=40` works the same as the upper form.
        monkeypatch.setenv("johnny_truncate_chars", "40")
        s = Settings(_env_file=None)
        assert s.johnny_truncate_chars == 40


class TestWebContextProcessor:
    """The web tier injects `settings` into every template render via
    `_inject_settings`. `base.html` uses `settings.johnny_truncate_chars`
    to set a CSS custom property. Smoke that the value reaches HTML."""

    def test_truncate_chars_reaches_rendered_html(
        self,
        engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("JOHNNY_TRUNCATE_CHARS", "33")
        get_settings.cache_clear()
        app = create_app(engine_factory=lambda: engine)
        client: FlaskClient = app.test_client()
        try:
            r = client.get("/")
            assert r.status_code == 200
            assert b"--truncate-width: 33ch" in r.data
        finally:
            # Don't leak the cached override into subsequent tests.
            get_settings.cache_clear()


class TestVersionFallback:
    """`johnny.__version__` reads from installed-package metadata.
    A source-tree-only checkout (`uv sync` skipped, or
    `johnny` excluded from the env) takes the PackageNotFoundError
    branch and surfaces `0.0.0+unknown` instead of crashing on
    import — the footer-version surface stays renderable."""

    def test_falls_back_to_unknown_when_metadata_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib.metadata as _meta
        from importlib.metadata import PackageNotFoundError

        def _raise(name: str) -> str:
            raise PackageNotFoundError(name)

        # Patch on importlib.metadata so the `from ... import version`
        # in johnny/__init__.py picks up our raising stub when reloaded.
        monkeypatch.setattr(_meta, "version", _raise)
        try:
            importlib.reload(johnny)
            assert johnny.__version__ == "0.0.0+unknown"
        finally:
            # Restore the real version for tests downstream that
            # assert on it (e.g. the footer-version smoke).
            monkeypatch.undo()
            importlib.reload(johnny)


class TestPlaybookStaleAfterSecondsSetting:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JOHNNY_PLAYBOOK_STALE_AFTER_SECONDS", raising=False)
        s = Settings(_env_file=None)
        assert s.johnny_playbook_stale_after_seconds == 3600

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JOHNNY_PLAYBOOK_STALE_AFTER_SECONDS", "7200")
        s = Settings(_env_file=None)
        assert s.johnny_playbook_stale_after_seconds == 7200

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("johnny_playbook_stale_after_seconds", "1800")
        s = Settings(_env_file=None)
        assert s.johnny_playbook_stale_after_seconds == 1800


class TestDefaultEngineFactory:
    """When no explicit `engine_factory` is passed to `create_app`,
    the web app falls back to `_default_engine_factory` which reads
    DATABASE_URL via Settings. Exercised through the public entry
    point so the production boot path stays covered."""

    def test_default_factory_builds_from_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
        get_settings.cache_clear()
        try:
            from johnny.web import _default_engine_factory

            engine = _default_engine_factory()
            assert engine.url.database == ":memory:"
            assert engine.dialect.name == "sqlite"
            engine.dispose()
        finally:
            get_settings.cache_clear()
