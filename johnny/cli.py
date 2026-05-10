"""johnny administrative CLI.

Standalone Click app, not Flask-CLI bound. Run via `johnny <subcommand>`
(installed as a poetry script entry-point) or
`poetry run johnny <subcommand>` during dev. Used by the johnny-tasks
sidecar for retention pruning.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import click
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from johnny.config import get_settings
from johnny.persistence import make_engine
from johnny.services.events import EventService
from johnny.services.groups import GroupService
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


@cli.command("dedupe-hosts")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the merge plan without writing.",
)
def dedupe_hosts(dry_run: bool) -> None:
    """Merge host rows that split into short-name + FQDN forms.

    Walks every host, computes its canonical FQDN from the latest
    fact snapshot using the same convergence ladder the callback
    plugin (v0.1.3+) now uses, groups by canonical, and merges any
    group with more than one row. Each merge runs in its own
    transaction; one bad group skips and prints a warning rather
    than rolling back the whole run.
    """
    engine = make_engine(get_settings().database_url)
    with Session(engine) as session:
        svc = HostService(session)
        groups = svc.find_merge_candidates()

        if not groups:
            click.echo("nothing to merge: 0 candidate groups")
            return

        merged = 0
        skipped = 0
        orphans_deleted = 0
        for g in groups:
            click.echo(f"canonical: {g.canonical_fqdn}")
            click.echo(
                f"  survivor: host_id={g.survivor.id} fqdn={g.survivor.fqdn}"
            )
            for o in g.orphans:
                click.echo(f"  orphan:   host_id={o.id} fqdn={o.fqdn}")
            click.echo(
                "  re-point: "
                f"host_facts_history={g.fk_counts['host_facts_history']}, "
                f"events={g.fk_counts['events']}, "
                f"playbook_hosts={g.fk_counts['playbook_hosts']}"
            )

            if dry_run:
                continue

            try:
                svc.merge_into(
                    g.survivor.id,
                    [o.id for o in g.orphans],
                    g.canonical_fqdn,
                )
                session.commit()
                merged += 1
                orphans_deleted += len(g.orphans)
            except SQLAlchemyError as e:
                session.rollback()
                click.echo(f"  SKIPPED: {e.__class__.__name__}: {e}")
                skipped += 1

        if dry_run:
            click.echo(
                f"summary: {len(groups)} groups, "
                f"{sum(len(g.orphans) for g in groups)} orphan hosts to "
                "delete (dry run, no changes written)"
            )
        else:
            click.echo(
                f"merged: {merged} groups, "
                f"{orphans_deleted} orphan hosts deleted; "
                f"skipped: {skipped} groups"
            )


@cli.command("groups-rebuild")
def groups_rebuild() -> None:
    """Rebuild groups + host_groups from playbook_hosts.groups_json.

    One-shot backfill for databases that pre-date the Groups model.
    Walks every playbook_hosts row in chronological order, upserts a
    Group per name observed, and replaces host_groups with the latest
    play's membership per host. Existing Group descriptions are
    preserved. Idempotent: safe to re-run.
    """
    engine = make_engine(get_settings().database_url)
    with Session(engine) as session:
        result = GroupService(session).rebuild_from_history()
        session.commit()
    click.echo(
        f"rebuilt: {result['groups']} groups, "
        f"{result['memberships']} host-group memberships"
    )


@cli.group("group")
def group_cli() -> None:
    """Per-group operations."""


@group_cli.command("describe")
@click.argument("name")
@click.argument("description")
def group_describe(name: str, description: str) -> None:
    """Set the free-text description on a known group.

    Pass an empty string to clear the description.
    """
    engine = make_engine(get_settings().database_url)
    with Session(engine) as session:
        try:
            GroupService(session).set_description(name, description or None)
        except LookupError as e:
            raise click.ClickException(str(e)) from None
        session.commit()
    click.echo(f"described: {name}")


if __name__ == "__main__":
    cli()
