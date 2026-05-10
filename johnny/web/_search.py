"""Scoped-search query parser.

Grammar:
  query     := token (WS+ token)*
  token     := atom (':' atom)?           # scope:value, or bare value
  atom      := bare | quoted | bracketed
  bare      := chars except WS, ':', ',', '"', '[', ']'
  quoted    := '"' chars-no-dquote '"'
  bracketed := '[' chars-no-rbracket ']'  # for values containing ':',
                                          # e.g. IPv6: ip:[fe80::1]
  value-side may use ','-separated atoms for an OR within the scope:
      status:failed,unreachable

Unknown scopes are demoted to bare terms (safe default — typing
`weh:foo` shouldn't silently drop the input). Bare terms are OR'd
across a per-list set of default columns; scoped terms AND together
with everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchQuery:
    """Parsed scoped-search expression.

    `scopes[name]` is the list of accepted values for that scope (OR
    within scope, AND across scopes). `bare` is the list of free
    terms; the caller decides which columns to OR them against.
    """

    scopes: dict[str, list[str]] = field(default_factory=dict)
    bare: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.scopes and not self.bare


def parse(raw: str, allowed_scopes: set[str] | None = None) -> SearchQuery:
    """Parse a search box value into a SearchQuery.

    `allowed_scopes` whitelists which `key:` prefixes are honored; any
    `key:` not in the set is folded back to a bare term (including the
    literal `key:value` text). Pass `None` to accept any scope.
    """
    if not raw or not raw.strip():
        return SearchQuery()

    scopes: dict[str, list[str]] = {}
    bare: list[str] = []
    for token in _tokenize(raw):
        key, value = _split_scope(token)
        if key is not None and (allowed_scopes is None or key in allowed_scopes):
            scopes.setdefault(key, []).extend(_split_values(value))
        else:
            bare.append(token)
    return SearchQuery(scopes=scopes, bare=bare)


def _tokenize(raw: str) -> list[str]:
    """Whitespace-split, but treat `"..."` and `[...]` as atomic.

    Inside paired delimiters, whitespace and `:` lose their meaning.
    The delimiters themselves are kept in the emitted token so the
    scope splitter can tell `key:[...]` apart from `key:bare`.
    """
    tokens: list[str] = []
    buf: list[str] = []
    closer: str | None = None
    for ch in raw:
        if closer is not None:
            buf.append(ch)
            if ch == closer:
                closer = None
            continue
        if ch == '"':
            buf.append(ch)
            closer = '"'
            continue
        if ch == "[":
            buf.append(ch)
            closer = "]"
            continue
        if ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf.clear()
            continue
        buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


def _split_scope(token: str) -> tuple[str | None, str]:
    """Split on the first unbracketed/unquoted ':'.

    Returns `(scope, value)` or `(None, token)` if the token has no
    scope prefix (which is the case for bare terms, quoted-only
    tokens, and bracketed-only tokens).
    """
    closer: str | None = None
    for i, ch in enumerate(token):
        if closer is not None:
            if ch == closer:
                closer = None
            continue
        if ch == '"':
            closer = '"'
            continue
        if ch == "[":
            closer = "]"
            continue
        if ch == ":":
            key = token[:i]
            value = token[i + 1 :]
            if not key:
                return None, token
            return key, value
    return None, token


def _split_values(value: str) -> list[str]:
    """Split a scope value on unbracketed/unquoted ',' for OR semantics.

    `status:failed,unreachable` -> `["failed", "unreachable"]`. A
    quoted or bracketed value passes through as a single atom with
    its delimiters stripped.
    """
    parts: list[str] = []
    buf: list[str] = []
    closer: str | None = None
    for ch in value:
        if closer is not None:
            buf.append(ch)
            if ch == closer:
                closer = None
            continue
        if ch == '"':
            closer = '"'
            buf.append(ch)
            continue
        if ch == "[":
            closer = "]"
            buf.append(ch)
            continue
        if ch == ",":
            if buf:
                parts.append(_strip_delims("".join(buf)))
                buf.clear()
            continue
        buf.append(ch)
    if buf:
        parts.append(_strip_delims("".join(buf)))
    return [p for p in parts if p]


def read_list_params(
    args: dict,
    sort_columns: dict,
    default_sort: str,
    default_dir: str,
    scopes: set[str] | None,
) -> tuple[str, str, str, SearchQuery]:
    """Parse `sort`, `dir`, `q` out of a request's query args.

    Returns `(sort, direction, raw_q, parsed_query)`. Unknown sort
    columns or direction values fall back to the defaults — the URL
    surface is user-typeable, and a bad param shouldn't 400 a browse.
    """
    sort = args.get("sort", default_sort)
    if sort not in sort_columns:
        sort = default_sort
    direction = args.get("dir", default_dir)
    if direction not in ("asc", "desc"):
        direction = default_dir
    raw_q = args.get("q", "")
    return sort, direction, raw_q, parse(raw_q, allowed_scopes=scopes)


def _strip_delims(atom: str) -> str:
    if len(atom) >= 2 and atom[0] == atom[-1] == '"':
        return atom[1:-1]
    if len(atom) >= 2 and atom[0] == "[" and atom[-1] == "]":
        return atom[1:-1]
    return atom
