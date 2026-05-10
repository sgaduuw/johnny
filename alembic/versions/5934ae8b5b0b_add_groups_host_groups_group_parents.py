"""add groups, host_groups, group_parents

Revision ID: 5934ae8b5b0b
Revises: 8f52091260cc
Create Date: 2026-05-10 10:13:52.685112

Three additive tables backing the Groups model. Source of truth for
"current membership" moves from `playbook_hosts.groups_json` (which
stays as the immutable per-run audit log) to `host_groups`, which is
maintained on ingest by GroupService.upsert_membership. See
CONTEXT.md "Groups model".

No STORED columns and no NOT NULL on populated rows, so the autogen
quirks documented in CLAUDE.md ("Schema migrations") don't apply: a
plain ALTER-equivalent (CREATE TABLE) is correct on both SQLite and
Postgres. Datetime columns use `sa.DateTime(timezone=True)` to match
the convention established in 2f7ea197035a; the runtime UtcDateTime
TypeDecorator wraps the same underlying SQL type.

`group_parents` ships empty. Population path (plugin-sent topology
vs operator CLI) is a separate change tracked in CONTEXT.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5934ae8b5b0b'
down_revision: Union[str, Sequence[str], None] = '8f52091260cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'groups',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('groups', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_groups_last_seen_at'),
            ['last_seen_at'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_groups_name'), ['name'], unique=True
        )

    op.create_table(
        'group_parents',
        sa.Column('child_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['child_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('child_id', 'parent_id'),
    )

    op.create_table(
        'host_groups',
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_playbook_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['last_playbook_id'], ['playbooks.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('host_id', 'group_id'),
    )


def downgrade() -> None:
    op.drop_table('host_groups')
    op.drop_table('group_parents')
    with op.batch_alter_table('groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_groups_name'))
        batch_op.drop_index(batch_op.f('ix_groups_last_seen_at'))

    op.drop_table('groups')
