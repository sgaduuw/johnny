"""add distribution, version, kernel projection columns

Revision ID: 78cbdc29c07b
Revises: 2f7ea197035a
Create Date: 2026-05-09 20:20:16.869560

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '78cbdc29c07b'
down_revision: Union[str, Sequence[str], None] = '2f7ea197035a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Adding STORED generated columns is the awkward case where SQLite
# and Postgres need different handling:
#
# * Postgres: ALTER TABLE ADD COLUMN ... GENERATED ALWAYS AS (...)
#   STORED works directly.
# * SQLite: ALTER TABLE ADD COLUMN can only add VIRTUAL generated
#   columns, never STORED ("cannot add a STORED column"). The
#   table must be recreated end-to-end via SQLite's documented
#   recreate pattern (https://sqlite.org/lang_altertable.html
#   section 7), the same one batch_alter_table tries to use, but
#   we have to do the INSERT ourselves because batch_alter_table's
#   auto-generated copy includes every existing generated column
#   and STORED columns can't accept explicit values.


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _upgrade_postgresql()
    else:
        _upgrade_sqlite()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _downgrade_postgresql()
    else:
        _downgrade_sqlite()


# -------- Postgres -----------------------------------------------------


def _upgrade_postgresql() -> None:
    op.execute(
        "ALTER TABLE hosts ADD COLUMN distribution VARCHAR(64) "
        "GENERATED ALWAYS AS ((last_facts ->> 'ansible_distribution')) STORED"
    )
    op.execute(
        "ALTER TABLE hosts ADD COLUMN distribution_version VARCHAR(64) "
        "GENERATED ALWAYS AS ((last_facts ->> 'ansible_distribution_version')) "
        "STORED"
    )
    op.execute(
        "ALTER TABLE hosts ADD COLUMN kernel VARCHAR(128) "
        "GENERATED ALWAYS AS ((last_facts ->> 'ansible_kernel')) STORED"
    )


def _downgrade_postgresql() -> None:
    op.execute("ALTER TABLE hosts DROP COLUMN kernel")
    op.execute("ALTER TABLE hosts DROP COLUMN distribution_version")
    op.execute("ALTER TABLE hosts DROP COLUMN distribution")


# -------- SQLite -------------------------------------------------------

# Mirrors the exact DDL alembic emits for the initial schema (verified
# against `.schema hosts` after a clean upgrade to 2f7ea197035a). The
# only delta vs. the pre-migration shape is the three appended
# generated columns.
_SQLITE_HOSTS_NEW = """
    CREATE TABLE hosts_new (
        id INTEGER NOT NULL,
        fqdn VARCHAR(255) NOT NULL,
        last_facts JSON NOT NULL,
        last_seen_at DATETIME,
        default_ipv4 VARCHAR(45) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_default_ipv4.address')) STORED,
        uptime_seconds INTEGER GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_uptime_seconds')) STORED,
        virt_role VARCHAR(16) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_virtualization_role')) STORED,
        virt_type VARCHAR(32) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_virtualization_type')) STORED,
        memtotal_mb INTEGER GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_memtotal_mb')) STORED,
        vcpus INTEGER GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_processor_vcpus')) STORED,
        distribution VARCHAR(64) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_distribution')) STORED,
        distribution_version VARCHAR(64) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_distribution_version')) STORED,
        kernel VARCHAR(128) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_kernel')) STORED,
        PRIMARY KEY (id)
    )
"""

# Pre-migration (initial schema) shape, used by the downgrade to
# recreate the table without the three new columns.
_SQLITE_HOSTS_OLD = """
    CREATE TABLE hosts_new (
        id INTEGER NOT NULL,
        fqdn VARCHAR(255) NOT NULL,
        last_facts JSON NOT NULL,
        last_seen_at DATETIME,
        default_ipv4 VARCHAR(45) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_default_ipv4.address')) STORED,
        uptime_seconds INTEGER GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_uptime_seconds')) STORED,
        virt_role VARCHAR(16) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_virtualization_role')) STORED,
        virt_type VARCHAR(32) GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_virtualization_type')) STORED,
        memtotal_mb INTEGER GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_memtotal_mb')) STORED,
        vcpus INTEGER GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_processor_vcpus')) STORED,
        PRIMARY KEY (id)
    )
"""


def _sqlite_recreate(create_new_sql: str) -> None:
    # PRAGMA defer_foreign_keys=ON works inside a transaction (unlike
    # PRAGMA foreign_keys=OFF which doesn't), and defers FK checks
    # until COMMIT. The DROP/RENAME of the parent table is invisible
    # to FK references in events / host_facts_history / playbook_hosts
    # because SQLite resolves FKs by name at write time, and the new
    # table replaces the old one under the same name.
    op.execute("PRAGMA defer_foreign_keys=ON")
    op.execute(create_new_sql)
    op.execute(
        "INSERT INTO hosts_new (id, fqdn, last_facts, last_seen_at) "
        "SELECT id, fqdn, last_facts, last_seen_at FROM hosts"
    )
    op.execute("DROP TABLE hosts")
    op.execute("ALTER TABLE hosts_new RENAME TO hosts")
    op.execute("CREATE UNIQUE INDEX ix_hosts_fqdn ON hosts (fqdn)")
    op.execute("CREATE INDEX ix_hosts_last_seen_at ON hosts (last_seen_at)")


def _upgrade_sqlite() -> None:
    _sqlite_recreate(_SQLITE_HOSTS_NEW)


def _downgrade_sqlite() -> None:
    _sqlite_recreate(_SQLITE_HOSTS_OLD)
