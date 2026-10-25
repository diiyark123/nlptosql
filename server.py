"""FastAPI backend for the web UI. Wraps the same pipeline the Streamlit app uses.

Run:  uvicorn server:app --port 8000      then open http://localhost:8000
"""
import math
import os
import threading
import uuid
from collections import OrderedDict
from datetime import date, datetime, time as dtime
from decimal import Decimal

import pymysql
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nl2sql.config import PROJECT_ROOT, load_settings
from nl2sql.db import load_schema, missing_allowlisted, run_read_only
from nl2sql.llm import make_generator
from nl2sql.pipeline import MAX_HISTORY_TURNS, Answer, answer_question

WEB_DIR = PROJECT_ROOT / "web"
MAX_CONVERSATIONS = 200  # in-memory history, oldest dropped first

EXAMPLE_QUESTIONS = [
    "Which aircraft are currently grounded or in maintenance?",
    "List components within 10% of their life limit, closest first",
    "Show overdue inspections with the aircraft and base airport",
    "Total parts cost and labour hours per aircraft in the last 6 months",
    "Which technicians signed off the most failed inspections this year?",
    "How many AOG events per ATA defect code in the last 12 months?",
]

settings = load_settings()
app = FastAPI(title="Maintenance Data Assistant")

_state = {"tables": None, "generator": None}
_conversations: "OrderedDict[str, list[Answer]]" = OrderedDict()
_lock = threading.Lock()


def tables():
    if _state["tables"] is None:
        _state["tables"] = load_schema(settings)
    return _state["tables"]


def generator():
    if _state["generator"] is None:
        _state["generator"] = make_generator()
    return _state["generator"]


def _plain(value):
    """Convert DB/pandas values into JSON-safe values."""
    if hasattr(value, "item"):  # numpy scalars
        value = value.item()
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, dtime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _serialize(ans: Answer) -> dict:
    out = {
        "question": ans.question,
        "status": ans.status,
        "explanation": ans.explanation,
        "sql": ans.sql,
        "message": ans.message,
        "clarifying_question": ans.clarifying_question,
        "truncated": ans.truncated,
        "attempts": ans.attempts,
        "tables_used": ans.tables_used,
        "two_step": ans.two_step,
        "is_follow_up": ans.is_follow_up,
        "total_seconds": round(ans.total_seconds, 2),
        "llm_seconds": round(ans.llm_seconds, 2),
        "db_seconds": round(ans.db_seconds, 3),
        "columns": [],
        "rows": [],
    }
    if ans.frame is not None:
        out["columns"] = [str(c) for c in ans.frame.columns]
        out["rows"] = [[_plain(v) for v in row] for row in ans.frame.itertuples(index=False, name=None)]
    return out


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


@app.get("/api/status")
def status():
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    model = getattr(generator(), "model", provider)
    try:
        ts = tables()
        db = {"connected": True, "database": settings.database, "user": settings.user, "tables": len(ts)}
    except pymysql.MySQLError as e:
        db = {"connected": False, "database": settings.database, "user": settings.user, "error": str(e)}
        ts = []
    return {
        "db": db,
        "llm": {"provider": provider, "model": model},
        "max_rows": settings.max_rows,
        "history_turns": MAX_HISTORY_TURNS,
        "allowlist": sorted(settings.allowed_tables) if settings.allowed_tables else None,
        "allowlist_missing": missing_allowlisted(settings, ts) if ts else [],
        "two_step": len(ts) > settings.selection_threshold,
        "examples": EXAMPLE_QUESTIONS,
    }


@app.get("/api/schema")
def schema():
    try:
        ts = tables()
    except pymysql.MySQLError as e:
        raise HTTPException(503, f"Cannot connect to MySQL: {e}") from e
    return [
        {
            "name": t.name,
            "comment": t.comment,
            "rows": t.row_count,
            "columns": [{"name": c.name, "type": c.data_type, "key": c.key, "comment": c.comment} for c in t.columns],
            "foreign_keys": [{"column": fk.column, "ref_table": fk.ref_table} for fk in t.foreign_keys],
        }
        for t in ts
    ]


@app.post("/api/schema/refresh")
def refresh_schema():
    _state["tables"] = None
    return {"tables": len(tables())}


@app.post("/api/ask")
def ask(req: AskRequest):
    try:
        ts = tables()
    except pymysql.MySQLError as e:
        raise HTTPException(503, f"Cannot connect to MySQL: {e}") from e

    conv_id = req.conversation_id or uuid.uuid4().hex
    with _lock:
        history = list(_conversations.get(conv_id, []))

    ans = answer_question(
        req.question.strip(),
        generator=generator(),
        tables=ts,
        database=settings.database,
        max_rows=settings.max_rows,
        execute=lambda sql: run_read_only(settings, sql),
        history=history,
        selection_threshold=settings.selection_threshold,
    )

    with _lock:
        _conversations.setdefault(conv_id, []).append(ans)
        _conversations.move_to_end(conv_id)
        while len(_conversations) > MAX_CONVERSATIONS:
            _conversations.popitem(last=False)

    return {"conversation_id": conv_id, "answer": _serialize(ans)}


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
