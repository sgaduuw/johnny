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


# Generated columns are added via raw ALTER TABLE rather than
# batch_alter_table. SQLite's batch implementation recreates the
# table and tries to INSERT every existing column into the new
# shape, which fails for the existing GENERATED columns
# (default_ipv4, virt_role, ...) because generated columns can't
# accept explicit values. Direct ALTER TABLE ADD COLUMN works on
# SQLite 3.31+ and Postgres for generated columns.

UP = [
    "ALTER TABLE hosts ADD COLUMN distribution VARCHAR(64) "
    "GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_distribution'))"
    " STORED",
    "ALTER TABLE hosts ADD COLUMN distribution_version VARCHAR(64) "
    "GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_distribution_version'))"
    " STORED",
    "ALTER TABLE hosts ADD COLUMN kernel VARCHAR(128) "
    "GENERATED ALWAYS AS (json_extract(last_facts, '$.ansible_kernel'))"
    " STORED",
]

DOWN = [
    "ALTER TABLE hosts DROP COLUMN kernel",
    "ALTER TABLE hosts DROP COLUMN distribution_version",
    "ALTER TABLE hosts DROP COLUMN distribution",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
