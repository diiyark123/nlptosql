"""MySQL access: schema introspection and read-only query execution."""
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import pandas as pd
import pymysql

from .config import Settings

# VARCHAR columns with this many distinct values or fewer get their values listed
# in the prompt, so the model matches real values ("Grounded", "BLR") instead of guessing.
LOW_CARDINALITY_LIMIT = 12


@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool
    key: str  # PRI / UNI / MUL / ''
    comment: str
    sample_values: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    comment: str
    row_count: int
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)


@contextmanager
def connect(settings: Settings):
    conn = pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        connect_timeout=10,
        charset="utf8mb4",
    )
    try:
        yield conn
    finally:
        conn.close()


def load_schema(settings: Settings) -> list[Table]:
    """Introspect tables, columns, comments, keys and low-cardinality values from information_schema."""
    db = settings.database
    with connect(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, table_comment, table_rows FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
            (db,),
        )
        tables = {name: Table(name, comment or "", rows or 0) for name, comment, rows in cur.fetchall()}
        if settings.allowed_tables is not None:
            # Allowlist: tables not listed are invisible to the prompt, the UI and the validator.
            tables = {n: t for n, t in tables.items() if n.lower() in settings.allowed_tables}

        cur.execute(
            "SELECT table_name, column_name, column_type, is_nullable, column_key, column_comment, data_type "
            "FROM information_schema.columns WHERE table_schema = %s ORDER BY table_name, ordinal_position",
            (db,),
        )
        text_columns = []
        for tname, cname, ctype, nullable, key, comment, data_type in cur.fetchall():
            if tname not in tables:
                continue
            col = Column(cname, ctype, nullable == "YES", key or "", comment or "")
            tables[tname].columns.append(col)
            if data_type in ("varchar", "char") and key not in ("PRI", "UNI"):
                text_columns.append((tname, col))

        cur.execute(
            "SELECT table_name, column_name, referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema = %s AND referenced_table_name IS NOT NULL",
            (db,),
        )
        for tname, cname, rtable, rcol in cur.fetchall():
            if tname in tables and rtable in tables:
                tables[tname].foreign_keys.append(ForeignKey(cname, rtable, rcol))

        # Identifiers come from information_schema, not user input, so backtick-quoting is safe here.
        for tname, col in text_columns:
            cur.execute(
                f"SELECT DISTINCT `{col.name}` FROM `{tname}` WHERE `{col.name}` IS NOT NULL "
                f"ORDER BY 1 LIMIT {LOW_CARDINALITY_LIMIT + 1}"
            )
            values = [str(r[0]) for r in cur.fetchall()]
            if len(values) <= LOW_CARDINALITY_LIMIT:
                col.sample_values = values

    return list(tables.values())


def missing_allowlisted(settings: Settings, tables: list[Table]) -> list[str]:
    """Allowlist entries that matched no table (usually a typo in ALLOWED_TABLES)."""
    if settings.allowed_tables is None:
        return []
    found = {t.name.lower() for t in tables}
    return sorted(settings.allowed_tables - found)


@dataclass
class QueryResult:
    frame: pd.DataFrame
    elapsed_s: float


def run_read_only(settings: Settings, sql: str) -> QueryResult:
    """Execute an already-validated SELECT inside a READ ONLY transaction with a server-side timeout."""
    with connect(settings) as conn, conn.cursor() as cur:
        cur.execute("SET SESSION max_execution_time = %s", (settings.timeout_ms,))
        cur.execute("START TRANSACTION READ ONLY")
        try:
            started = time.perf_counter()
            cur.execute(sql)
            rows = cur.fetchall()
            elapsed = time.perf_counter() - started
            columns = [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.rollback()
    return QueryResult(pd.DataFrame(rows, columns=columns), elapsed)
