"""johnny administrative CLI.

Standalone Click app, not Flask-CLI bound. Run via `johnny <subcommand>`
(installed as a poetry script entry-point) or
`poetry run johnny <subcommand>` during dev. Used by the johnny-tasks
sidecar for retention pruning.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import click
from sqlalchemy.orm import Session

from johnny.config import get_settings
from johnny.persistence import make_engine
from johnny.services.events import EventService
from johnny.services.hosts import HostService


@click.group()
def cli() -> None:
    """johnny administrative CLI."""


@cli.command()
@click.option(
    "--older-than-days",
    default=30,
    show_default=True,
    type=click.IntRange(min=1),
    help="Delete fact-history rows and events strictly older than N days.",
)
def prune(older_than_days: int) -> None:
    """Delete fact history and event rows older than --older-than-days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    engine = make_engine(get_settings().database_url)
    with Session(engine) as session:
        deleted_facts = HostService(session).prune_history(cutoff)
        deleted_events = EventService(session).prune(cutoff)
        session.commit()
    click.echo(
        f"pruned: {deleted_facts} fact-history rows, "
        f"{deleted_events} events (cutoff: {cutoff.isoformat()})"
    )


if __name__ == "__main__":
    cli()
