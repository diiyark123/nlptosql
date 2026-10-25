import pytest

from nl2sql.validator import InvalidQueryError, UnsafeQueryError, validate_sql

TABLES = {"aircraft", "components", "inspections", "maintenance_logs", "technicians"}


def check(sql, max_rows=500):
    return validate_sql(sql, TABLES, "aircraft_mx", max_rows)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM aircraft",
    "SELECT tail_number FROM aircraft WHERE status = 'Grounded'",
    "WITH due AS (SELECT * FROM inspections WHERE completed_date IS NULL) SELECT * FROM due",
    "SELECT a.tail_number, SUM(m.parts_cost) FROM aircraft a "
    "JOIN maintenance_logs m ON m.aircraft_id = a.aircraft_id GROUP BY a.tail_number",
    "SELECT tail_number FROM aircraft UNION SELECT serial_number FROM components",
    "SELECT * FROM aircraft_mx.aircraft",
    "SELECT * FROM (SELECT aircraft_id FROM aircraft) AS t",
])
def test_allows_read_queries(sql):
    assert check(sql).upper().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize("sql", [
    "DELETE FROM aircraft",
    "UPDATE aircraft SET status = 'Retired'",
    "INSERT INTO technicians (full_name) VALUES ('x')",
    "DROP TABLE aircraft",
    "TRUNCATE aircraft",
    "ALTER TABLE aircraft ADD COLUMN x INT",
    "CREATE TABLE t (id INT)",
    "GRANT ALL ON *.* TO someone",
    "SET GLOBAL max_connections = 1",
    "CALL some_procedure()",
    "SELECT * FROM aircraft; DROP TABLE aircraft",
    "SELECT * FROM aircraft INTO OUTFILE '/tmp/out.csv'",
    "SELECT * INTO @x FROM aircraft",
    "SELECT * FROM aircraft FOR UPDATE",
    "SELECT SLEEP(10)",
    "SELECT BENCHMARK(1000000, MD5('x'))",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT * FROM mysql.user",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM other_db.aircraft",
    "SELECT /*!50000 SLEEP(5) */ 1",
    "SELECT * FROM aircraft WHERE aircraft_id IN (SELECT SLEEP(1))",
])
def test_blocks_unsafe_queries(sql):
    with pytest.raises(UnsafeQueryError):
        check(sql)


def test_unknown_table_is_fixable():
    with pytest.raises(InvalidQueryError, match="Unknown table 'planes'"):
        check("SELECT * FROM planes")


def test_empty_query_is_fixable():
    with pytest.raises(InvalidQueryError):
        check("   ")


def test_adds_limit_when_missing():
    assert check("SELECT * FROM aircraft", max_rows=100).endswith("LIMIT 100")


def test_keeps_smaller_limit():
    assert check("SELECT * FROM aircraft LIMIT 5").endswith("LIMIT 5")


def test_caps_larger_limit():
    assert check("SELECT * FROM aircraft LIMIT 100000", max_rows=500).endswith("LIMIT 500")


def test_strips_comments_from_executed_sql():
    out = check("SELECT tail_number /* harmless? */ FROM aircraft -- trailing")
    assert "--" not in out


def test_cte_name_does_not_need_allowlist():
    check("WITH recent AS (SELECT * FROM maintenance_logs) SELECT COUNT(*) FROM recent")
