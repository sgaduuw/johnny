"""Seed the dev DB with a realistic-ish fleet snapshot for clicking through.

Two playbooks (deploy.yml ~26h ago, db-rotate.yml ~1h ago) so the audit
log has more than one observation per host. ~9 hosts spread across
overlapping groups to make the cards interesting; six groups get a
description so the description path is visible too.

Run via: `poetry run python scripts/seed_mock.py`

Re-running stacks new playbook rows on top of the existing audit log
(fresh UUIDs each call). Hosts upsert by fqdn, so they don't duplicate,
but `playbook_hosts` and `events` grow. That's intentional: the demo
exercises the audit-vs-derived-view split — current group membership
reflects the latest play, while the audit log preserves earlier
observations.

To start clean, delete `johnny.db` and re-run `alembic upgrade head`.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from johnny.config import get_settings
from johnny.contracts.v1 import (
    FactRecord,
    FactsBatch,
    HostStatCounts,
    PlaybookFinish,
    PlaybookStart,
)
from johnny.persistence import make_engine
from johnny.services.groups import GroupService
from johnny.services.ingest import CallbackIngest

NOW = datetime.now(timezone.utc)


def host(fqdn, groups, ipv4, distro, ver, kernel, mem_mb, vcpus,
         virt_type="kvm", uptime_days=3):
    return FactRecord(
        fqdn=fqdn,
        inventory_hostname=fqdn.split(".")[0],
        groups=groups,
        ansible_facts={
            "ansible_default_ipv4": {"address": ipv4},
            "ansible_uptime_seconds": uptime_days * 86400 + 3600,
            "ansible_virtualization_role": "guest" if virt_type != "none" else "host",
            "ansible_virtualization_type": virt_type,
            "ansible_memtotal_mb": mem_mb,
            "ansible_processor_vcpus": vcpus,
            "ansible_distribution": distro,
            "ansible_distribution_version": ver,
            "ansible_kernel": kernel,
        },
    )


PLAY1_HOSTS = [
    host("web1.example.com",  ["all", "linux", "debian", "webservers"],
         "10.0.0.11", "Debian", "12.5", "6.1.0-26-amd64", 4096, 2, uptime_days=14),
    host("web2.example.com",  ["all", "linux", "debian", "webservers"],
         "10.0.0.12", "Debian", "12.5", "6.1.0-26-amd64", 4096, 2, uptime_days=12),
    host("web3.example.com",  ["all", "linux", "debian", "webservers"],
         "10.0.0.13", "Debian", "12.5", "6.1.0-26-amd64", 8192, 4, uptime_days=2),
    host("lb1.example.com",   ["all", "linux", "debian", "loadbalancers", "webservers"],
         "10.0.0.20", "Debian", "12.5", "6.1.0-26-amd64", 2048, 2, uptime_days=21),
    host("mon1.example.com",  ["all", "linux", "debian", "monitoring"],
         "10.0.0.30", "Debian", "12.5", "6.1.0-26-amd64", 8192, 4, uptime_days=45),
    host("bsd1.example.com",  ["all", "openbsd", "edge"],
         "10.0.0.40", "OpenBSD", "7.5", "OpenBSD 7.5 GENERIC.MP#82 amd64", 2048, 2,
         virt_type="none", uptime_days=120),
]

PLAY2_HOSTS = [
    host("db1.example.com",  ["all", "linux", "ubuntu", "dbservers"],
         "10.0.0.51", "Ubuntu", "24.04", "6.8.0-31-generic", 16384, 4, uptime_days=7),
    host("db2.example.com",  ["all", "linux", "ubuntu", "dbservers"],
         "10.0.0.52", "Ubuntu", "24.04", "6.8.0-31-generic", 16384, 4, uptime_days=7),
    host("cache1.example.com", ["all", "linux", "ubuntu", "dbservers", "cache"],
         "10.0.0.60", "Ubuntu", "22.04", "5.15.0-105-generic", 32768, 8,
         uptime_days=180),
]


def run_play(ingest, name, when, hosts, inventory):
    pid = uuid4()
    ingest.start_playbook(PlaybookStart(
        id=pid, name=name, inventory_sources=[inventory],
        started_at=when, user="ansible",
    ))
    ingest.ingest_facts(pid, FactsBatch(captured_at=when, hosts=hosts))
    ingest.finish_playbook(pid, PlaybookFinish(
        finished_at=when + timedelta(minutes=2),
        stats={
            h.fqdn: HostStatCounts(
                ok=3, changed=1, failed=0, unreachable=0,
                skipped=0, rescued=0, ignored=0,
            ) for h in hosts
        },
    ))
    return pid


def main() -> None:
    engine = make_engine(get_settings().database_url)
    with Session(engine) as session:
        ingest = CallbackIngest(session)
        run_play(ingest, "deploy.yml", NOW - timedelta(days=1, hours=2),
                 PLAY1_HOSTS, "inventories/prod.yml")
        run_play(ingest, "db-rotate.yml", NOW - timedelta(hours=1),
                 PLAY2_HOSTS, "inventories/data.yml")

        grp = GroupService(session)
        grp.set_description("all", "Every host that has ever reported.")
        grp.set_description("webservers", "Front-end HTTP servers (nginx).")
        grp.set_description("dbservers", "Postgres primaries and replicas.")
        grp.set_description("monitoring", "Netdata/Prometheus collectors.")
        grp.set_description("loadbalancers", "HAProxy front of the webservers.")
        grp.set_description("openbsd", "OpenBSD edge hosts; pf, relayd.")

        session.commit()

    print("seeded.")


if __name__ == "__main__":
    main()
