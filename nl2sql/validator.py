"""Read-only SQL validation.

Every query the model writes is parsed into a syntax tree with sqlglot and checked
before it touches the database. Two kinds of failure:

* UnsafeQueryError  - a write, DDL, multi-statement or dangerous function. Blocked, never retried.
* InvalidQueryError - unparseable SQL or an unknown table. Sent back to the model to fix.

The SQL that is executed is re-generated from the validated tree rather than the raw
model output, so comments or MySQL "/*! ... */" executable comments can't smuggle in
anything the validator didn't see.
"""
import logging
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# sqlglot logs a warning whenever it falls back to a raw Command (e.g. GRANT, CALL); we reject those anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class UnsafeQueryError(Exception):
    """The query would modify data or is otherwise not allowed. Do not retry."""


class InvalidQueryError(Exception):
    """The query is malformed or references unknown objects. The model may fix it."""


READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Into, exp.Lock, exp.Set, exp.Use, exp.Grant,
    exp.Transaction, exp.Commit, exp.Rollback,
)

# Functions that read the server filesystem, stall the server, or take locks.
FORBIDDEN_FUNCTIONS = {
    "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS",
    "IS_FREE_LOCK", "IS_USED_LOCK", "MASTER_POS_WAIT", "SOURCE_POS_WAIT", "SYS_EXEC", "SYS_EVAL",
}

SYSTEM_SCHEMAS = {"mysql", "information_schema", "performance_schema", "sys"}


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).upper()
    return node.sql_name().upper()


def validate_sql(sql: str, allowed_tables: set[str], database: str, max_rows: int) -> str:
    """Return a safe, LIMIT-capped version of `sql`, or raise UnsafeQueryError / InvalidQueryError."""
    if not sql or not sql.strip():
        raise InvalidQueryError("Empty query.")
    if "/*!" in sql:
        raise UnsafeQueryError("MySQL executable comments (/*! ... */) are not allowed.")
    # Checked on the raw text because sqlglot can't parse every INTO form, and a parse
    # error would otherwise be treated as fixable and retried.
    if re.search(r"\bINTO\s+(OUTFILE|DUMPFILE|@)", sql, re.IGNORECASE):
        raise UnsafeQueryError("SELECT ... INTO (files or variables) is not allowed.")

    try:
        statements = [s for s in sqlglot.parse(sql.strip().rstrip(";"), read="mysql") if s is not None]
    except ParseError as e:
        raise InvalidQueryError(f"SQL could not be parsed: {e}") from e

    if len(statements) != 1:
        raise UnsafeQueryError(f"Exactly one statement is allowed, got {len(statements)}.")
    tree = statements[0]

    if not isinstance(tree, READ_ROOTS):
        raise UnsafeQueryError(f"Only SELECT queries are allowed, got {tree.key.upper()}.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeQueryError(f"{node.key.upper()} is not allowed in a read-only query.")
        if isinstance(node, exp.Func) and _function_name(node) in FORBIDDEN_FUNCTIONS:
            raise UnsafeQueryError(f"Function {_function_name(node)}() is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        schema = table.db.lower()
        if not name:
            raise UnsafeQueryError("Table functions are not allowed.")
        if schema and (schema in SYSTEM_SCHEMAS or schema != database.lower()):
            raise UnsafeQueryError(f"Access to schema '{table.db}' is not allowed.")
        if name in cte_names and not schema:
            continue
        if name not in allowed:
            raise InvalidQueryError(
                f"Unknown table '{table.name}'. Available tables: {', '.join(sorted(allowed))}."
            )

    tree = _cap_limit(tree, max_rows)
    return tree.sql(dialect="mysql")


def _cap_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    """Add LIMIT max_rows when missing; lower an existing LIMIT that exceeds it."""
    limit = tree.args.get("limit")
    if limit is None:
        return tree.limit(max_rows)
    value = limit.expression
    if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= max_rows:
        return tree
    return tree.limit(max_rows)
