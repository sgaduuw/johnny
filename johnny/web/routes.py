"""View functions for johnny-web."""

from __future__ import annotations

from uuid import UUID

from flask import Flask, abort, g, render_template

from johnny.persistence import Playbook
from johnny.services.events import EventService
from johnny.services.hosts import HostService
from johnny.services.plays import PlayService


def register_routes(app: Flask) -> None:
    @app.route("/")
    def index() -> str:
        hosts = HostService(g.session).list_all()
        return render_template("hosts_list.html", hosts=hosts)

    @app.route("/hosts/<fqdn>")
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
