"""Pytest wrapper around _mig_seed.py / _mig_smoke_assert.py.

The CI migration-smoke step
(`.github/workflows/ci.yml`, "Migration smoke against a populated
DB") is the real test: upgrade-to-PREV, seed, upgrade-head, assert
— that order catches "migration fails on populated rows." This
pytest is a thinner local guard that runs `upgrade-head → seed →
assert` against a temp SQLite, so the helpers themselves don't
drift silently between CI runs (a column rename on HEAD trips
here, not waiting until next push).

What this catches:
  * a future migration that renames a column the helpers reference
    (they'd ImportError or KeyError here, not silently in CI).
  * a helper edit that breaks the round-trip projection assertion.

What this does NOT catch (and only the CI smoke step does):
  * a migration that fails when applied against existing data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from tests import _mig_seed, _mig_smoke_assert


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> tuple[Config, str]:
    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(repo_root / "alembic.ini"))
    db_path = tmp_path / "smoke.db"
    url = f"sqlite:///{db_path}"
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg, url


# The seeder / asserter / alembic each open their own engine and don't
# dispose explicitly — that's fine in CI (process exits) but leaves
# pytest's unraisable-exception hook surfacing a ResourceWarning at
# tmp_path cleanup. Suppress at this test only; legitimate connection
# leaks elsewhere still surface.
@pytest.mark.filterwarnings(
    "ignore:.*unclosed database.*:ResourceWarning"
)
@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)
def test_seed_and_assert_helpers_survive_head_schema(
    alembic_cfg: tuple[Config, str],
) -> None:
    cfg, url = alembic_cfg
    command.upgrade(cfg, "head")
    _mig_seed.main(url)
    # main() exits via sys.exit on failure; if it returns, every check
    # passed (row counts + projection columns + foreign_key_check).
    _mig_smoke_assert.main(url)
