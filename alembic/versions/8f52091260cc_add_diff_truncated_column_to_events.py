"""add diff_truncated column to events

Revision ID: 8f52091260cc
Revises: 78cbdc29c07b
Create Date: 2026-05-09 21:28:18.259612

Wire-contract change: TaskEvent gains diff_truncated alongside the
existing stdout_truncated, restoring symmetry between the two
truncation paths. See johnny-callback issue #7.

The events table has no generated columns, so this is a plain
ALTER TABLE ADD COLUMN on both SQLite (3.31+) and Postgres — no
12-step recreate dance needed (cf. 78cbdc29c07b on hosts). Server
default of `false` is required because we're adding a NOT NULL
column to a table that already has rows in deployed databases.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f52091260cc'
down_revision: Union[str, Sequence[str], None] = '78cbdc29c07b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'diff_truncated',
                sa.Boolean(),
                nullable=False,
                # `sa.false()` compiles to dialect-portable false:
                # `0` on SQLite, `false` on Postgres. Required so
                # existing event rows get a value at upgrade time.
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_column('diff_truncated')
