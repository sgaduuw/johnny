"""Dialect-portable JSON path extraction for use in Computed() columns.

SQLite emits `json_extract(col, '$.path')`; Postgres emits the
equivalent `-> / ->>` chain with optional `::cast`. One declaration
in the model, no alembic-per-dialect overrides.

Both `column` and `path` are developer-controlled identifiers baked
into DDL, never user input — string interpolation here is safe.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

JsonAsType = Literal["text", "int", "bigint", "bool"]


class json_path(FunctionElement):  # noqa: N801 — SQL function naming convention
    inherit_cache = True

    def __init__(self, column: str, path: str, as_type: JsonAsType = "text") -> None:
        super().__init__()
        self.column = column
        self.path = path
        self.as_type = as_type


@compiles(json_path, "sqlite")
def _json_path_sqlite(element: json_path, compiler: Any, **kw: Any) -> str:
    return f"json_extract({element.column}, '$.{element.path}')"


@compiles(json_path, "postgresql")
def _json_path_pg(element: json_path, compiler: Any, **kw: Any) -> str:  # pragma: no cover
    # Postgres-only DDL emission. Exercised at table-create time
    # against a real PG engine; unreachable from the SQLite test
    # suite, hence the pragma. Wire-equivalence with the SQLite
    # form is the responsibility of the (future) PG-deployment
    # smoke once that environment exists.
    parts = element.path.split(".")
    expr = element.column
    for p in parts[:-1]:
        expr = f"{expr} -> '{p}'"
    expr = f"({expr} ->> '{parts[-1]}')"
    cast = {
        "int": "::integer",
        "bigint": "::bigint",
        "bool": "::boolean",
        "text": "",
    }[element.as_type]
    return f"({expr}{cast})" if cast else expr
