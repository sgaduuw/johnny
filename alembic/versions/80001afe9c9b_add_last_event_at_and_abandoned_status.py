"""add last_event_at and ABANDONED status

Revision ID: 80001afe9c9b
Revises: 5934ae8b5b0b
Create Date: 2026-06-11 00:29:02.985012

Adds the `playbooks.last_event_at` column (server-receipt time of the
most recent callback POST for a play) and extends `playbook_status`
with `abandoned`. Together these underpin the stale-RUNNING detector
(see johnny issue #26).

Dialect-aware in two places:
  1. The enum extension: Postgres uses ALTER TYPE ADD VALUE inside an
     autocommit block (ALTER TYPE cannot run in a transaction); SQLite
     stores the enum as a CHECK constraint, so batch_alter_table runs
     the documented 12-step recreate dance to widen it. Safe on this
     table because `playbooks` has no STORED generated columns (cf.
     CLAUDE.md autogen quirk #2).
  2. The column add carries `server_default=CURRENT_TIMESTAMP` so the
     NOT NULL constraint is satisfied on existing rows. A follow-up
     UPDATE then overwrites that initial stamp with COALESCE(finished_at,
     started_at) per row so ancient stuck-RUNNING plays don't suddenly
     look fresh post-migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80001afe9c9b'
down_revision: Union[str, Sequence[str], None] = '5934ae8b5b0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 1. Add the last_event_at column + index + backfill.
    #
    # The column carries server_default=CURRENT_TIMESTAMP so fresh
    # CREATE TABLE paths and Postgres ALTER TABLE both satisfy NOT NULL
    # on existing rows. SQLite's ALTER TABLE ADD COLUMN rejects
    # non-constant defaults like CURRENT_TIMESTAMP ("Cannot add a
    # column with non-constant default"), so on SQLite we run the
    # batch_alter_table recreate dance instead. Safe here because
    # `playbooks` has no STORED generated columns (per CLAUDE.md
    # autogen quirk #2).
    #
    # The follow-up UPDATE overwrites the initial CURRENT_TIMESTAMP
    # stamp with COALESCE(finished_at, started_at) per row so
    # ancient stuck-RUNNING plays don't suddenly look fresh
    # post-migration. PlayService.start() sets last_event_at
    # explicitly for new rows going forward.
    column = sa.Column(
        "last_event_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    if dialect == "sqlite":
        with op.batch_alter_table("playbooks") as batch_op:
            batch_op.add_column(column)
    else:
        op.add_column("playbooks", column)
    op.create_index(
        "ix_playbooks_last_event_at",
        "playbooks",
        ["last_event_at"],
    )
    op.execute(
        "UPDATE playbooks "
        "SET last_event_at = COALESCE(finished_at, started_at)"
    )

    # 2. Extend playbook_status enum to include 'ABANDONED'.
    #
    # The on-disk enum carries the *member names* (uppercase) rather
    # than the StrEnum values (lowercase) because SQLAlchemy's
    # `Enum(PlaybookStatus, ...)` defaults to `.name` for the enum
    # labels. The initial migration codified this with
    # `Enum('RUNNING', 'FINISHED', 'FAILED', name='playbook_status')`;
    # we extend that set with `'ABANDONED'`, not `'abandoned'`, to
    # stay consistent with what's actually stored.
    if dialect == "postgresql":
        # ALTER TYPE ADD VALUE cannot run inside a transaction block.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE playbook_status ADD VALUE 'ABANDONED'")
    else:
        # SQLite: SQLAlchemy 2.0 defaults `Enum.create_constraint` to
        # False, so there's no CHECK constraint to widen and the column
        # is a VARCHAR storing the label text. Without a CHECK there is
        # nothing strictly to alter, but the batch_alter_table branch
        # exists so the migration has a defined SQLite codepath if SA's
        # default ever flips back to True. Safe because `playbooks` has
        # no STORED generated columns (per CLAUDE.md autogen quirk #2).
        with op.batch_alter_table("playbooks") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(
                    "RUNNING", "FINISHED", "FAILED",
                    name="playbook_status",
                ),
                type_=sa.Enum(
                    "RUNNING", "FINISHED", "FAILED", "ABANDONED",
                    name="playbook_status",
                ),
                existing_nullable=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Postgres has no ALTER TYPE DROP VALUE. Recreating the enum
        # type to drop one value is invasive and out of scope for a
        # downgrade. Leave the enum value in place; downgrading just
        # removes the column. New code wouldn't write 'ABANDONED'
        # anyway because the StrEnum no longer carries it.
        pass
    else:
        with op.batch_alter_table("playbooks") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(
                    "RUNNING", "FINISHED", "FAILED", "ABANDONED",
                    name="playbook_status",
                ),
                type_=sa.Enum(
                    "RUNNING", "FINISHED", "FAILED",
                    name="playbook_status",
                ),
                existing_nullable=False,
            )

    # Note: drop_column discards every row's last_event_at without
    # preserving the value elsewhere. There is no adjacent column to
    # write it to, so a downgrade-after-running-in-prod intentionally
    # loses the sweeper's stamp. Operators reverting need to also
    # roll back the application code that populates the column.
    op.drop_index("ix_playbooks_last_event_at", table_name="playbooks")
    op.drop_column("playbooks", "last_event_at")
