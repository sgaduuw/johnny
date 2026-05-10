"""Seed a tiny but representative dataset against a DB at the
*previous* migration revision.

Used by the CI "migration smoke" step (issue #6): the runner upgrades
to PREV, runs this script, then runs `alembic upgrade head` and
re-runs `_mig_smoke_assert` against the upgraded DB. That order
catches migrations that fail on populated rows — a class of bug
that `alembic check` and the existing CI's `upgrade head` against
an empty DB cannot see (and that we shipped three times in v0.2.x
before adding this step).

Insert counts are deterministic so `_mig_smoke_assert` can compare
against fixed expectations. Inserts are raw SQL (not the ORM models)
because the ORM reflects HEAD's schema, and we're targeting PREV's
schema here.

Touch this file when a new migration changes columns the seeder
inserts into. The footprint is small and the breakage is loud.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text


SEED_COUNTS = {
    "playbooks": 1,
    "hosts": 2,
    "host_facts_history": 2,
    "playbook_hosts": 2,
    "events": 2,
}

# Picked to exercise every projection column on `hosts` so the
# asserter can verify they survived the upgrade.
HOST_FACTS = [
    {
        "fqdn": "smoke1.example.com",
        "ansible_default_ipv4": {"address": "10.0.0.1"},
        "ansible_uptime_seconds": 86400,
        "ansible_virtualization_role": "guest",
        "ansible_virtualization_type": "kvm",
        "ansible_memtotal_mb": 4096,
        "ansible_processor_vcpus": 2,
        "ansible_distribution": "Debian",
        "ansible_distribution_version": "12.5",
        "ansible_kernel": "6.1.0-26-amd64",
    },
    {
        "fqdn": "smoke2.example.com",
        "ansible_default_ipv4": {"address": "10.0.0.2"},
        "ansible_uptime_seconds": 7200,
        "ansible_virtualization_role": "host",
        "ansible_virtualization_type": "NA",
        "ansible_memtotal_mb": 16384,
        "ansible_processor_vcpus": 8,
        "ansible_distribution": "Ubuntu",
        "ansible_distribution_version": "24.04",
        "ansible_kernel": "6.8.0-31-generic",
    },
]


def main(database_url: str) -> None:
    engine = create_engine(database_url)
    now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc).isoformat()
    pb_id = uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        conn.execute(
            text(
                "INSERT INTO playbooks "
                "(id, name, inventory_sources, started_at, finished_at, "
                " user, status, tags, skip_tags, \"limit\", check_mode, stats) "
                "VALUES (:id, :name, :inv, :started, :finished, :user, "
                " :status, :tags, :skip_tags, :limit, :cm, :stats)"
            ),
            {
                "id": pb_id,
                "name": "smoke.yml",
                "inv": json.dumps(["inventory.yml"]),
                "started": now,
                "finished": now,
                "user": "smoke-runner",
                "status": "finished",
                "tags": json.dumps([]),
                "skip_tags": json.dumps([]),
                "limit": None,
                "cm": False,
                "stats": json.dumps({}),
            },
        )
        for facts in HOST_FACTS:
            fqdn = facts["fqdn"]
            result = conn.execute(
                text(
                    "INSERT INTO hosts (fqdn, last_facts, last_seen_at) "
                    "VALUES (:fqdn, :facts, :seen)"
                ),
                {"fqdn": fqdn, "facts": json.dumps(facts), "seen": now},
            )
            host_id = result.lastrowid
            conn.execute(
                text(
                    "INSERT INTO host_facts_history "
                    "(host_id, captured_at, facts, playbook_id) "
                    "VALUES (:hid, :cap, :facts, :pid)"
                ),
                {
                    "hid": host_id,
                    "cap": now,
                    "facts": json.dumps(facts),
                    "pid": pb_id,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO playbook_hosts "
                    "(playbook_id, host_id, inventory_hostname, groups) "
                    "VALUES (:pid, :hid, :inv, :groups)"
                ),
                {
                    "pid": pb_id,
                    "hid": host_id,
                    "inv": fqdn.split(".", 1)[0],
                    "groups": json.dumps(["all", "smoke"]),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO events "
                    "(event_uuid, playbook_id, host_id, task_name, "
                    " task_action, status, started_at, duration_ms, "
                    " stdout, stdout_truncated, diff, diff_truncated) "
                    "VALUES (:eid, :pid, :hid, :tn, :ta, :st, :sa, :dur, "
                    " :stdout, :stt, :diff, :dt)"
                ),
                {
                    "eid": uuid.uuid4().hex,
                    "pid": pb_id,
                    "hid": host_id,
                    "tn": "smoke task",
                    "ta": "debug",
                    "st": "ok",
                    "sa": now,
                    "dur": 100,
                    "stdout": "",
                    "stt": False,
                    "diff": None,
                    "dt": False,
                },
            )
    print(f"seeded {SEED_COUNTS} into {database_url}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tests/_mig_seed.py <DATABASE_URL>")
    main(sys.argv[1])
