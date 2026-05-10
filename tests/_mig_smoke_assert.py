"""Verify a DB seeded by `_mig_seed.py` survives `alembic upgrade head`.

Pairs with `_mig_seed.py` for the CI migration smoke step (issue #6).
After the upgrade runs, this script:

  1. Runs `PRAGMA foreign_key_check` and aborts on any orphan rows.
  2. Compares row counts in the originally-seeded tables against the
     fixed counts the seeder inserted, so a migration that drops
     rows fails loudly here.
  3. Verifies generated/projection columns on `hosts` carry values
     extracted from `last_facts`, so a migration that breaks the
     projection layer (a STORED column rebuilt with the wrong
     expression) fails here.

Exits non-zero on any check failure.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

from tests._mig_seed import HOST_FACTS, SEED_COUNTS


def main(database_url: str) -> None:
    engine = create_engine(database_url)
    failures: list[str] = []
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        orphans = conn.execute(text("PRAGMA foreign_key_check")).all()
        if orphans:
            failures.append(f"foreign_key_check found orphan rows: {orphans}")

        for table, expected in SEED_COUNTS.items():
            actual = conn.execute(
                text(f"SELECT COUNT(*) FROM {table}")
            ).scalar_one()
            if actual != expected:
                failures.append(
                    f"row-count drift in {table}: expected {expected}, got {actual}"
                )

        for facts in HOST_FACTS:
            row = conn.execute(
                text(
                    "SELECT default_ipv4, distribution, distribution_version, "
                    "       kernel, virt_role, memtotal_mb, vcpus, uptime_seconds "
                    "FROM hosts WHERE fqdn = :fqdn"
                ),
                {"fqdn": facts["fqdn"]},
            ).one()
            checks = {
                "default_ipv4": (row[0], facts["ansible_default_ipv4"]["address"]),
                "distribution": (row[1], facts["ansible_distribution"]),
                "distribution_version": (row[2], facts["ansible_distribution_version"]),
                "kernel": (row[3], facts["ansible_kernel"]),
                "virt_role": (row[4], facts["ansible_virtualization_role"]),
                "memtotal_mb": (row[5], facts["ansible_memtotal_mb"]),
                "vcpus": (row[6], facts["ansible_processor_vcpus"]),
                "uptime_seconds": (row[7], facts["ansible_uptime_seconds"]),
            }
            for col, (got, want) in checks.items():
                if got != want:
                    failures.append(
                        f"projection drift on hosts.{col} for "
                        f"{facts['fqdn']}: expected {want!r}, got {got!r}"
                    )

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        sys.exit(1)
    print(f"smoke ok: {database_url}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/_mig_smoke_assert.py <DATABASE_URL>")
    main(sys.argv[1])
