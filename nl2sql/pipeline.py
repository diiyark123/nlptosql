"""Question -> (table selection) -> SQL -> validation -> execution, with one self-correction retry.

Supports follow-up questions: earlier turns of the conversation are replayed to the LLM so
"now only BLR" or an answer to a clarifying question builds on the previous query.
"""
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Callable

import pandas as pd
import pymysql

from .config import PROJECT_ROOT
from .db import Table
from .llm import GenerationError
from .prompt import build_system_prompt, render_catalog, with_related
from .validator import InvalidQueryError, UnsafeQueryError, validate_sql

AUDIT_LOG = PROJECT_ROOT / "logs" / "audit.jsonl"
MAX_HISTORY_TURNS = 3  # earlier turns replayed for follow-ups; keeps the prompt small


@dataclass
class Answer:
    question: str
    status: str  # ok | not_answerable | needs_clarification | blocked | error
    explanation: str = ""
    sql: str = ""
    message: str = ""
    clarifying_question: str = ""
    frame: pd.DataFrame | None = None
    truncated: bool = False  # True when more rows existed than max_rows
    attempts: int = 0
    tables_used: list[str] = field(default_factory=list)
    two_step: bool = False  # True when the LLM picked tables first (large schema)
    is_follow_up: bool = False
    llm_seconds: float = 0.0
    db_seconds: float = 0.0
    total_seconds: float = 0.0
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0})


def answer_question(
    question: str,
    *,
    generator,
    tables: list[Table],
    database: str,
    max_rows: int,
    execute: Callable[[str], "object"],
    history: list[Answer] = (),
    selection_threshold: int = 30,
    max_attempts: int = 2,
) -> Answer:
    """Run the full pipeline. `execute(sql)` must return an object with .frame and .elapsed_s."""
    started = time.perf_counter()
    history = [h for h in history if h.status in ("ok", "not_answerable", "needs_clarification")][-MAX_HISTORY_TURNS:]
    result = Answer(question=question, status="error", is_follow_up=bool(history))
    try:
        _run(result, question, history, generator, tables, database, max_rows, execute,
             selection_threshold, max_attempts)
    except GenerationError as e:
        result.status, result.message = "error", str(e)
    result.total_seconds = time.perf_counter() - started
    _audit(result)
    return result


def _result_note(turn: Answer) -> str:
    """What the previous query returned, so the model can refine it sensibly."""
    if turn.status != "ok" or turn.frame is None:
        return ""
    cols = ", ".join(map(str, turn.frame.columns))
    more = " (cut off at the row limit)" if turn.truncated else ""
    return f"(Your previous query returned {len(turn.frame)} rows{more} with columns: {cols}.)\n"


def _conversation(question: str, history: list[Answer]) -> list[dict]:
    """Replay earlier turns as user/assistant messages, then the new question."""
    messages, note = [], ""
    for turn in history:
        messages.append({"role": "user", "content": f"{note}Question: {turn.question}"})
        messages.append({"role": "assistant", "content": json.dumps({
            "answerable": turn.status == "ok",
            "sql": turn.sql,
            "explanation": turn.explanation,
            "clarifying_question": turn.clarifying_question,
        })})
        note = _result_note(turn)
    # Today's date goes in the user turn, not the system prompt, so the system prompt stays cacheable.
    messages.append({"role": "user", "content": f"Today's date: {date.today().isoformat()}\n\n{note}Question: {question}"})
    return messages


def _run(result, question, history, generator, tables, database, max_rows, execute,
         selection_threshold, max_attempts):
    prompt_tables = tables
    if len(tables) > selection_threshold:
        # Step 1 of two: the schema is too big to send whole, so the LLM first picks the tables it needs
        # from a compact catalog. Step 2 below then sees full detail for only those tables.
        result.two_step = True
        context = " / ".join([h.question for h in history] + [question])
        t0 = time.perf_counter()
        selected, reply = generator.select_tables(render_catalog(tables), context, [t.name for t in tables])
        result.llm_seconds += time.perf_counter() - t0
        result.tokens["input"] += reply.input_tokens
        result.tokens["output"] += reply.output_tokens
        names = {t.name for t in tables}
        selected = list(dict.fromkeys(selected + [n for h in history for n in h.tables_used if n in names]))
        if not selected:
            result.status = "not_answerable"
            result.explanation = "None of the available tables seem to hold data for this question."
            return
        prompt_tables = with_related(tables, selected)
    result.tables_used = [t.name for t in prompt_tables]

    system_prompt = build_system_prompt(prompt_tables)
    # The validator accepts every table the app may see, not just the selected ones.
    allowed_tables = {t.name for t in tables}
    messages = _conversation(question, history)

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        t0 = time.perf_counter()
        try:
            proposal = generator.propose(system_prompt, messages)
        finally:
            result.llm_seconds += time.perf_counter() - t0
        result.tokens["input"] += proposal.input_tokens
        result.tokens["output"] += proposal.output_tokens
        result.explanation = proposal.explanation
        result.sql = proposal.sql

        if not proposal.answerable:
            if proposal.clarifying_question:
                result.status, result.clarifying_question = "needs_clarification", proposal.clarifying_question
            else:
                result.status = "not_answerable"
            return

        try:
            # Shown SQL is capped at max_rows; the executed SQL fetches one extra row to detect truncation.
            safe_sql = validate_sql(proposal.sql, allowed_tables, database, max_rows)
            probe_sql = validate_sql(proposal.sql, allowed_tables, database, max_rows + 1)
        except UnsafeQueryError as e:
            result.status, result.message = "blocked", f"Query blocked by safety checks: {e}"
            return
        except InvalidQueryError as e:
            feedback = f"The query was rejected: {e}"
        else:
            result.sql = safe_sql
            try:
                run = execute(probe_sql)
            except pymysql.MySQLError as e:
                feedback = f"MySQL returned an error: {e.args[-1] if e.args else e}"
            else:
                frame = run.frame
                if len(frame) > max_rows:
                    frame, result.truncated = frame.head(max_rows), True
                result.status, result.frame, result.db_seconds = "ok", frame, run.elapsed_s
                result.message = ""
                return

        # Self-correction: show the model its previous answer and the error, keeping the conversation append-only.
        result.message = feedback
        messages.append({"role": "assistant", "content": proposal.assistant_content})
        messages.append({"role": "user", "content": f"{feedback}\nPlease return a corrected query."})


def _audit(result: Answer) -> None:
    record = {k: v for k, v in asdict(result).items() if k != "frame"}
    record["rows"] = None if result.frame is None else len(result.frame)
    record["timestamp"] = datetime.now().isoformat(timespec="seconds")
    AUDIT_LOG.parent.mkdir(exist_ok=True)
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")
