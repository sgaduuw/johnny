"""View functions for johnny-web."""

from __future__ import annotations

from uuid import UUID

from flask import Flask, abort, g, render_template, request

from johnny.persistence import Playbook
from johnny.services.events import EventService
from johnny.services.groups import (
    GROUP_DEFAULT_DIR,
    GROUP_DEFAULT_SORT,
    GROUP_SEARCH_SCOPES,
    GROUP_SORT_COLUMNS,
    HOST_DEFAULT_DIR,
    HOST_DEFAULT_SORT,
    HOST_SEARCH_SCOPES,
    HOST_SORT_COLUMNS,
    GroupService,
)
from johnny.services.hosts import HostService
from johnny.services.plays import (
    PLAY_DEFAULT_DIR,
    PLAY_DEFAULT_SORT,
    PLAY_SEARCH_SCOPES,
    PLAY_SORT_COLUMNS,
    PlayService,
)
from johnny.web._partial import pick
from johnny.web._search import read_list_params


def register_routes(app: Flask) -> None:
    @app.route("/")
    def index() -> str:
        sort, direction, query_str, query = read_list_params(
            request.args,
            GROUP_SORT_COLUMNS,
            GROUP_DEFAULT_SORT,
            GROUP_DEFAULT_DIR,
            GROUP_SEARCH_SCOPES,
        )
        groups = GroupService(g.session).list_with_counts(
            sort=sort, direction=direction, query=query
        )
        return render_template(
            pick("groups_index.html", "_groups_grid.html"),
            groups=groups,
            query_str=query_str,
            current_sort=sort,
            current_dir=direction,
        )

    @app.route("/g/<group_name>/")
    def group_detail(group_name: str) -> str:
        svc = GroupService(g.session)
        group = svc.get_by_name(group_name)
        if group is None:
            abort(404)
        sort, direction, query_str, query = read_list_params(
            request.args,
            HOST_SORT_COLUMNS,
            HOST_DEFAULT_SORT,
            HOST_DEFAULT_DIR,
            HOST_SEARCH_SCOPES,
        )
        hosts = svc.hosts_in(group, sort=sort, direction=direction, query=query)
        return render_template(
            pick("group_detail.html", "_group_hosts_table.html"),
            group=group,
            hosts=hosts,
            query_str=query_str,
            current_sort=sort,
            current_dir=direction,
        )

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
        sort, direction, query_str, query = read_list_params(
            request.args,
            PLAY_SORT_COLUMNS,
            PLAY_DEFAULT_SORT,
            PLAY_DEFAULT_DIR,
            PLAY_SEARCH_SCOPES,
        )
        plays = PlayService(g.session).list_recent(
            sort=sort, direction=direction, query=query
        )
        return render_template(
            pick("playbooks_list.html", "_playbooks_table.html"),
            plays=plays,
            query_str=query_str,
            current_sort=sort,
            current_dir=direction,
        )

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
