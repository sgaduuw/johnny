"""View functions for johnny-web."""

from __future__ import annotations

from uuid import UUID

from flask import Flask, abort, g, render_template

from johnny.persistence import Playbook
from johnny.services.events import EventService
from johnny.services.groups import GroupService
from johnny.services.hosts import HostService
from johnny.services.plays import PlayService


def register_routes(app: Flask) -> None:
    @app.route("/")
    def index() -> str:
        groups = GroupService(g.session).list_with_counts()
        return render_template("groups_index.html", groups=groups)

    @app.route("/g/<group_name>/")
    def group_detail(group_name: str) -> str:
        svc = GroupService(g.session)
        group = svc.get_by_name(group_name)
        if group is None:
            abort(404)
        hosts = svc.hosts_in(group)
        return render_template("group_detail.html", group=group, hosts=hosts)

    @app.route("/h/<fqdn>/")
    def host_detail(fqdn: str) -> str:
        svc = HostService(g.session)
        host = svc.latest(fqdn)
        if host is None:
            abort(404)
        history = svc.history(fqdn)
        return render_template("host_detail.html", host=host, history=history)

    @app.route("/playbooks")
    def playbooks_list() -> str:
        plays = PlayService(g.session).list_recent()
        return render_template("playbooks_list.html", plays=plays)

    @app.route("/playbooks/<uuid:playbook_id>")
    def playbook_detail(playbook_id: UUID) -> str:
        play = g.session.get(Playbook, playbook_id)
        if play is None:
            abort(404)
        roster = PlayService(g.session).roster(playbook_id)
        events = EventService(g.session).for_play(playbook_id)
        return render_template(
            "playbook_detail.html", play=play, roster=roster, events=events
        )
