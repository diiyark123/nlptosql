import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    database: str
    user: str
    password: str
    max_rows: int
    timeout_ms: int
    allowed_tables: frozenset[str] | None  # None = every table in the database
    selection_threshold: int  # above this many tables, the LLM first picks the relevant ones


def load_settings(admin: bool = False) -> Settings:
    """Read connection settings from the environment.

    The app always uses the read-only account; admin=True is only for db/setup_db.py.
    """
    prefix = "MYSQL_ADMIN" if admin else "MYSQL_READONLY"
    return Settings(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE", "aircraft_mx"),
        user=os.getenv(f"{prefix}_USER", "root" if admin else "nl2sql_reader"),
        password=os.getenv(f"{prefix}_PASSWORD", ""),
        max_rows=int(os.getenv("MAX_RESULT_ROWS", "500")),
        timeout_ms=int(os.getenv("QUERY_TIMEOUT_MS", "15000")),
        allowed_tables=_parse_allowlist(os.getenv("ALLOWED_TABLES", "")),
        selection_threshold=int(os.getenv("SCHEMA_SELECTION_THRESHOLD", "30")),
    )


def _parse_allowlist(raw: str) -> frozenset[str] | None:
    names = frozenset(n.strip().lower() for n in raw.split(",") if n.strip())
    return names or None
