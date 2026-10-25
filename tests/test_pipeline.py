"""Pipeline and prompt tests with a fake LLM and fake database - no API key or MySQL needed."""
from types import SimpleNamespace

import pandas as pd
import pymysql
import pytest

from nl2sql import pipeline
from nl2sql.db import Column, ForeignKey, Table
from nl2sql.config import _parse_allowlist
from nl2sql.llm import GenerationError, JSONReply, SQLProposal
from nl2sql.pipeline import answer_question
from nl2sql.prompt import build_system_prompt, render_catalog, render_schema, with_related

TABLES = [
    Table("aircraft", "Fleet register", 25, [
        Column("aircraft_id", "int", False, "PRI", ""),
        Column("tail_number", "varchar(10)", False, "UNI", "Aircraft registration"),
        Column("base_airport", "char(3)", False, "", "IATA code", ["BLR", "DEL"]),
    ]),
    Table("components", "Tracked parts", 300, [
        Column("component_id", "int", False, "PRI", ""),
        Column("aircraft_id", "int", True, "MUL", ""),
    ], [ForeignKey("aircraft_id", "aircraft", "aircraft_id")]),
]


@pytest.fixture(autouse=True)
def tmp_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "AUDIT_LOG", tmp_path / "audit.jsonl")


class FakeGenerator:
    def __init__(self, *responses, selected=None):
        self.responses = list(responses)
        self.calls = []
        self.selected = selected or []
        self.selection_calls = []
        self.system_prompts = []

    def select_tables(self, catalog, question, table_names):
        self.selection_calls.append(catalog)
        return self.selected, JSONReply({"tables": self.selected}, "", 3, 1)

    def first_turn(self, question):
        return {"role": "user", "content": question}

    def propose(self, system_prompt, messages):
        self.calls.append(list(messages))
        self.system_prompts.append(system_prompt)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        answerable, sql, *rest = r
        clarify = rest[0] if rest else ""
        return SQLProposal(answerable, sql, "explanation", clarify, [{"type": "text", "text": sql}], 10, 5)


def run(generator, execute=None, tables=TABLES, threshold=30, question="q", history=()):
    execute = execute or (lambda sql: SimpleNamespace(frame=pd.DataFrame({"n": [1]}), elapsed_s=0.01))
    return answer_question(
        question, generator=generator, tables=tables, database="aircraft_mx", max_rows=50,
        execute=execute, selection_threshold=threshold, history=history,
    )


def test_render_schema_includes_comments_values_and_relationships():
    text = render_schema(TABLES)
    assert "tail_number varchar(10) UNIQUE NOT NULL -- Aircraft registration" in text
    assert "[values: BLR, DEL]" in text
    assert "components.aircraft_id -> aircraft.aircraft_id" in text


def test_system_prompt_has_no_date_so_it_stays_cacheable():
    assert "Today" not in build_system_prompt(TABLES)


def test_happy_path_caps_rows():
    gen = FakeGenerator((True, "SELECT * FROM aircraft"))
    ans = run(gen)
    assert ans.status == "ok" and ans.attempts == 1
    assert ans.sql.endswith("LIMIT 50")


def test_write_request_is_blocked_without_retry():
    gen = FakeGenerator((True, "DELETE FROM aircraft"))
    ans = run(gen)
    assert ans.status == "blocked" and len(gen.calls) == 1


def test_not_answerable_passes_explanation_through():
    ans = run(FakeGenerator((False, "")))
    assert ans.status == "not_answerable"


def test_unknown_table_triggers_one_correction():
    gen = FakeGenerator((True, "SELECT * FROM planes"), (True, "SELECT * FROM aircraft"))
    ans = run(gen)
    assert ans.status == "ok" and ans.attempts == 2
    retry_messages = gen.calls[1]
    assert retry_messages[-2]["role"] == "assistant"
    assert "Unknown table 'planes'" in retry_messages[-1]["content"]


def test_mysql_error_triggers_correction_then_gives_up():
    def failing(sql):
        raise pymysql.err.OperationalError(1054, "Unknown column 'x' in 'field list'")

    gen = FakeGenerator((True, "SELECT x FROM aircraft"), (True, "SELECT y FROM aircraft"))
    ans = run(gen, execute=failing)
    assert ans.status == "error" and ans.attempts == 2
    assert "Unknown column" in ans.message


def test_generation_error_is_reported():
    ans = run(FakeGenerator(GenerationError("API key is missing")))
    assert ans.status == "error" and "API key" in ans.message


def test_audit_log_written(tmp_path):
    run(FakeGenerator((True, "SELECT * FROM aircraft")))
    assert '"status": "ok"' in (tmp_path / "audit.jsonl").read_text()


# ---------- allowlist and two-step table selection ----------

def test_allowlist_parsing():
    assert _parse_allowlist("") is None
    assert _parse_allowlist(" Aircraft, components ,") == frozenset({"aircraft", "components"})


def test_small_schema_sends_everything_in_one_step():
    gen = FakeGenerator((True, "SELECT * FROM aircraft"))
    ans = run(gen)
    assert not ans.two_step and gen.selection_calls == []
    assert ans.tables_used == ["aircraft", "components"]


def test_large_schema_picks_tables_first():
    gen = FakeGenerator((True, "SELECT * FROM components"), selected=["components"])
    ans = run(gen, threshold=1)
    assert ans.two_step and ans.status == "ok"
    # components links to aircraft, so aircraft is added for joins
    assert ans.tables_used == ["aircraft", "components"]
    assert "components -- Tracked parts | columns: component_id, aircraft_id" in gen.selection_calls[0]


def test_large_schema_prompt_contains_only_selected_tables():
    gen = FakeGenerator((True, "SELECT * FROM aircraft"), selected=["aircraft"])
    run(gen, threshold=1)
    assert "TABLE aircraft" in gen.system_prompts[0]
    assert "TABLE components" not in gen.system_prompts[0]


def test_no_relevant_tables_is_not_answerable():
    gen = FakeGenerator(selected=[])
    ans = run(gen, threshold=1)
    assert ans.status == "not_answerable" and gen.calls == []


def test_with_related_adds_foreign_key_targets():
    assert [t.name for t in with_related(TABLES, ["components"])] == ["aircraft", "components"]
    assert [t.name for t in with_related(TABLES, ["aircraft"])] == ["aircraft"]


def test_catalog_is_compact():
    assert len(render_catalog(TABLES)) < len(render_schema(TABLES))


# ---------- truncation warning ----------

def test_truncation_detected_when_more_rows_than_limit():
    executed = []

    def many_rows(sql):
        executed.append(sql)
        return SimpleNamespace(frame=pd.DataFrame({"n": range(51)}), elapsed_s=0.01)

    ans = run(FakeGenerator((True, "SELECT * FROM aircraft")), execute=many_rows)
    assert ans.truncated and len(ans.frame) == 50
    assert executed[0].endswith("LIMIT 51")  # fetched one extra row to detect truncation
    assert ans.sql.endswith("LIMIT 50")  # the SQL shown to the user keeps the real cap


def test_no_truncation_when_result_fits():
    ans = run(FakeGenerator((True, "SELECT * FROM aircraft")))
    assert not ans.truncated


# ---------- clarifying questions ----------

def test_clarifying_question_status():
    gen = FakeGenerator((False, "", "Best by most inspections passed, or by hours logged?"))
    ans = run(gen)
    assert ans.status == "needs_clarification"
    assert "inspections passed" in ans.clarifying_question


# ---------- follow-up questions ----------

def ok_turn(question, sql):
    return pipeline.Answer(question=question, status="ok", sql=sql, explanation="e",
                           frame=pd.DataFrame({"tail_number": ["VT-RAA"]}))


def test_follow_up_replays_previous_turn():
    gen = FakeGenerator((True, "SELECT * FROM aircraft WHERE base_airport = 'BLR'"))
    prev = ok_turn("Show grounded aircraft", "SELECT * FROM aircraft WHERE status = 'Grounded'")
    ans = run(gen, question="only BLR", history=[prev])
    messages = gen.calls[0]
    assert ans.is_follow_up
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "Show grounded aircraft" in messages[0]["content"]
    assert "status = 'Grounded'" in messages[1]["content"]
    assert "returned 1 rows with columns: tail_number" in messages[2]["content"]
    assert messages[2]["content"].endswith("Question: only BLR")


def test_answer_to_clarifying_question_is_a_follow_up():
    prev = pipeline.Answer(question="Who is the best technician?", status="needs_clarification",
                           clarifying_question="By inspections passed or hours logged?")
    gen = FakeGenerator((True, "SELECT * FROM aircraft"))
    run(gen, question="inspections passed", history=[prev])
    assert "By inspections passed or hours logged?" in gen.calls[0][1]["content"]


def test_failed_turns_are_not_replayed_and_history_is_capped():
    failed = pipeline.Answer(question="broken", status="error", message="x")
    turns = [ok_turn(f"q{i}", "SELECT 1") for i in range(5)] + [failed]
    gen = FakeGenerator((True, "SELECT * FROM aircraft"))
    run(gen, question="next", history=turns)
    replayed = [m["content"] for m in gen.calls[0] if m["role"] == "user"][:-1]
    assert len(replayed) == pipeline.MAX_HISTORY_TURNS
    assert not any("broken" in c for c in replayed)


def test_first_question_carries_todays_date():
    gen = FakeGenerator((True, "SELECT * FROM aircraft"))
    run(gen)
    assert gen.calls[0][0]["content"].startswith("Today's date:")
