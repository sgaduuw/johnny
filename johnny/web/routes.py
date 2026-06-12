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
from johnny.web._partial import is_htmx_request, pick
from johnny.web._search import read_list_params


def _read_page(args) -> int:
    # Page is an HTMX-driven append mechanism, not a deep-linkable
    # view; clamp to >= 1 and silently fall back to 1 on garbage.
    # Non-HX-Request callers never see page > 1 anyway (see
    # _pick_list_template); we still honour the value at the service
    # layer so HX-Request load-more works deterministically.
    try:
        return max(1, int(args.get("page", "1")))
    except ValueError:
        return 1


def _pick_list_template(
    page: int, full: str, table_partial: str, rows_partial: str
) -> str:
    # Three render paths per list route:
    #   1. Non-HX request                       -> full page
    #   2. HX-Request, page == 1 (search/sort)  -> full table partial
    #      (search form + first batch + Load-more row if has_more)
    #   3. HX-Request, page  > 1 (Load-more)    -> rows-only partial
    #      (next batch of rows + new Load-more row, or nothing)
    if not is_htmx_request():
        return full
    return rows_partial if page > 1 else table_partial


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
        page = _read_page(request.args)
        hosts, has_more = svc.hosts_in(
            group, sort=sort, direction=direction, query=query, page=page
        )
        levels = svc.ancestry_levels(group)
        children = svc.children_of(group)
        return render_template(
            _pick_list_template(
                page,
                "group_detail.html",
                "_group_hosts_table.html",
                "_group_hosts_rows.html",
            ),
            group=group,
            hosts=hosts,
            has_more=has_more,
            page=page,
            query_str=query_str,
            current_sort=sort,
            current_dir=direction,
            levels=levels,
            children=children,
        )

    @app.route("/h/<fqdn>/")
    def host_detail(fqdn: str) -> str:
        svc = HostService(g.session)
        host = svc.latest(fqdn)
        if host is None:
            abort(404)
        history = svc.history(fqdn)
        return render_template(
            pick("host_detail.html", "_host_detail_live.html"),
            host=host,
            history=history,
        )

    @app.route("/h/<fqdn>/diff")
    def host_diff(fqdn: str) -> str:
        # Query params (?a=<id>&b=<id>) instead of path components so a
        # form with two <select>s can submit to a static action without
        # client-side URL assembly.
        try:
            id_a = int(request.args.get("a", ""))
            id_b = int(request.args.get("b", ""))
        except ValueError:
            abort(400)
        svc = HostService(g.session)
        host = svc.latest(fqdn)
        if host is None:
            abort(404)
        try:
            a, b, diff_lines = svc.diff(fqdn, id_a, id_b)
        except LookupError:
            abort(404)
        return render_template(
            "host_diff.html",
            host=host,
            a=a,
            b=b,
            diff_lines=diff_lines,
        )

    @app.route("/playbooks")
    def playbooks_list() -> str:
        sort, direction, query_str, query = read_list_params(
            request.args,
            PLAY_SORT_COLUMNS,
            PLAY_DEFAULT_SORT,
            PLAY_DEFAULT_DIR,
            PLAY_SEARCH_SCOPES,
        )
        page = _read_page(request.args)
        plays, has_more = PlayService(g.session).list_recent(
            sort=sort, direction=direction, query=query, page=page
        )
        return render_template(
            _pick_list_template(
                page,
                "playbooks_list.html",
                "_playbooks_table.html",
                "_playbooks_rows.html",
            ),
            plays=plays,
            has_more=has_more,
            page=page,
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
