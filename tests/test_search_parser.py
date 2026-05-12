"""Unit tests for the scoped-search parser."""

from __future__ import annotations

from johnny.web._search import parse, read_list_params


class TestBareTerms:
    def test_empty_input_is_empty_query(self) -> None:
        q = parse("")
        assert q.is_empty()
        q = parse("   ")
        assert q.is_empty()

    def test_single_bare_term(self) -> None:
        q = parse("nginx")
        assert q.bare == ["nginx"]
        assert q.scopes == {}

    def test_multiple_bare_terms(self) -> None:
        q = parse("nginx deploy redis")
        assert q.bare == ["nginx", "deploy", "redis"]


class TestScopes:
    def test_scoped_value(self) -> None:
        q = parse("user:eelco", allowed_scopes={"user"})
        assert q.scopes == {"user": ["eelco"]}
        assert q.bare == []

    def test_unknown_scope_demoted_to_bare(self) -> None:
        # `weh:foo` shouldn't silently disappear when the list only
        # accepts {"user", "status"} — round-trip as a bare token.
        q = parse("weh:foo nginx", allowed_scopes={"user", "status"})
        assert q.scopes == {}
        assert q.bare == ["weh:foo", "nginx"]

    def test_none_allowed_scopes_accepts_all(self) -> None:
        q = parse("anything:goes", allowed_scopes=None)
        assert q.scopes == {"anything": ["goes"]}

    def test_scopes_and_bare_terms_coexist(self) -> None:
        q = parse(
            "user:eelco status:failed nginx", allowed_scopes={"user", "status"}
        )
        assert q.scopes == {"user": ["eelco"], "status": ["failed"]}
        assert q.bare == ["nginx"]

    def test_comma_or_within_scope(self) -> None:
        q = parse("status:failed,unreachable", allowed_scopes={"status"})
        assert q.scopes == {"status": ["failed", "unreachable"]}

    def test_repeated_scope_accumulates(self) -> None:
        # `user:a user:b` is treated the same as `user:a,b`.
        q = parse("user:a user:b", allowed_scopes={"user"})
        assert q.scopes == {"user": ["a", "b"]}

    def test_leading_colon_is_bare(self) -> None:
        # `:foo` has no scope key; round-trip as bare.
        q = parse(":foo")
        assert q.bare == [":foo"]


class TestQuoting:
    def test_quoted_phrase_with_whitespace(self) -> None:
        q = parse('name:"deploy nginx"', allowed_scopes={"name"})
        assert q.scopes == {"name": ["deploy nginx"]}

    def test_bare_quoted_phrase(self) -> None:
        q = parse('"a b c"')
        assert q.bare == ['"a b c"']

    def test_quoted_protects_colon(self) -> None:
        # Without quotes this would split on `:`; quoted, the colon is
        # part of the value, not a scope separator.
        q = parse('name:"a:b"', allowed_scopes={"name"})
        assert q.scopes == {"name": ["a:b"]}


class TestBracketed:
    def test_ipv6_style_bracketed_value(self) -> None:
        q = parse("ip:[fe80::1]", allowed_scopes={"ip"})
        assert q.scopes == {"ip": ["fe80::1"]}

    def test_bare_bracketed_value(self) -> None:
        # Bracketed bare token survives intact (delimiters preserved
        # so the caller can still see it's a literal request).
        q = parse("[fe80::1]")
        assert q.bare == ["[fe80::1]"]

    def test_bracket_protects_colon_and_comma(self) -> None:
        # Inside brackets `:` and `,` are literal characters.
        q = parse("ip:[fe80::1,2]", allowed_scopes={"ip"})
        assert q.scopes == {"ip": ["fe80::1,2"]}

    def test_bracketed_within_comma_list(self) -> None:
        q = parse("ip:[fe80::1],[fe80::2]", allowed_scopes={"ip"})
        assert q.scopes == {"ip": ["fe80::1", "fe80::2"]}


class TestReadListParams:
    """read_list_params is the request-shim that the list routes use:
    pulls sort/dir/q out of args, runs `parse` for q, falls back to
    defaults on unknown values. Several web tests exercise this
    transitively; a unit test locks the contract directly."""

    SORT_COLUMNS = {"name": object(), "started_at": object()}

    def test_returns_defaults_for_empty_args(self) -> None:
        sort, direction, raw_q, q = read_list_params(
            {},
            sort_columns=self.SORT_COLUMNS,
            default_sort="name",
            default_dir="asc",
            scopes=None,
        )
        assert sort == "name"
        assert direction == "asc"
        assert raw_q == ""
        assert q.is_empty()

    def test_unknown_sort_falls_back_to_default(self) -> None:
        sort, *_ = read_list_params(
            {"sort": "exploded"},
            sort_columns=self.SORT_COLUMNS,
            default_sort="name",
            default_dir="asc",
            scopes=None,
        )
        assert sort == "name"

    def test_unknown_direction_falls_back_to_default(self) -> None:
        _, direction, *_ = read_list_params(
            {"dir": "sideways"},
            sort_columns=self.SORT_COLUMNS,
            default_sort="name",
            default_dir="desc",
            scopes=None,
        )
        assert direction == "desc"

    def test_known_sort_and_direction_kept(self) -> None:
        sort, direction, *_ = read_list_params(
            {"sort": "started_at", "dir": "desc"},
            sort_columns=self.SORT_COLUMNS,
            default_sort="name",
            default_dir="asc",
            scopes=None,
        )
        assert sort == "started_at"
        assert direction == "desc"

    def test_q_round_trips_with_allowed_scopes(self) -> None:
        _, _, raw_q, q = read_list_params(
            {"q": "user:eelco bare-term"},
            sort_columns=self.SORT_COLUMNS,
            default_sort="name",
            default_dir="asc",
            scopes={"user"},
        )
        assert raw_q == "user:eelco bare-term"
        assert q.scopes == {"user": ["eelco"]}
        assert q.bare == ["bare-term"]

    def test_scope_not_in_allowed_is_demoted_to_bare(self) -> None:
        # Mirrors parse() behavior so the route's filter loop doesn't
        # see scopes it isn't equipped to handle.
        _, _, _, q = read_list_params(
            {"q": "danger:zone"},
            sort_columns=self.SORT_COLUMNS,
            default_sort="name",
            default_dir="asc",
            scopes={"user"},
        )
        assert q.scopes == {}
        assert q.bare == ["danger:zone"]
