"""List every MySQL database with its tables, and the columns of one database.

    python db/show_databases.py                 # all databases + their tables
    python db/show_databases.py aircraft_mx     # also show columns for aircraft_mx

Uses the MYSQL_ADMIN_* credentials from .env (read-only queries against information_schema).
"""
import sys
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nl2sql.config import load_settings  # noqa: E402

SYSTEM = ("mysql", "information_schema", "performance_schema", "sys")


def main():
    detail_db = sys.argv[1] if len(sys.argv) > 1 else None
    s = load_settings(admin=True)
    conn = pymysql.connect(host=s.host, port=s.port, user=s.user, password=s.password)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.schema_name, t.table_name, t.table_rows FROM information_schema.schemata s "
            "LEFT JOIN information_schema.tables t ON t.table_schema = s.schema_name "
            "ORDER BY s.schema_name, t.table_name"
        )
        current = None
        for db, table, rows in cur.fetchall():
            if db != current:
                current = db
                print(f"\n{db}{'  (system)' if db in SYSTEM else ''}")
            if table and db not in SYSTEM:
                print(f"    {table:<30} ~{rows or 0} rows")

        if detail_db:
            cur.execute(
                "SELECT table_name, column_name, column_type, column_key, column_comment "
                "FROM information_schema.columns WHERE table_schema = %s "
                "ORDER BY table_name, ordinal_position",
                (detail_db,),
            )
            current = None
            for table, col, ctype, key, comment in cur.fetchall():
                if table != current:
                    current = table
                    print(f"\n[{detail_db}.{table}]")
                print(f"    {col:<22} {ctype:<45} {key:<4} {comment}")
    conn.close()


if __name__ == "__main__":
    main()
