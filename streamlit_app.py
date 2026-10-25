"""Streamlit UI: ask maintenance data questions in plain English, with follow-ups."""
import os

import pymysql
import streamlit as st

from nl2sql.config import load_settings
from nl2sql.db import load_schema, missing_allowlisted, run_read_only
from nl2sql.llm import make_generator
from nl2sql.pipeline import MAX_HISTORY_TURNS, answer_question

st.set_page_config(page_title="Maintenance Data Assistant", page_icon="✈️", layout="wide")

EXAMPLE_QUESTIONS = [
    "Which aircraft are currently grounded or in maintenance?",
    "List components within 10% of their life limit, closest first",
    "Show overdue inspections with the aircraft and base airport",
    "Total parts cost and labour hours per aircraft in the last 6 months",
    "Which technicians signed off the most failed inspections this year?",
    "How many AOG events per ATA defect code in the last 12 months?",
]

settings = load_settings()


@st.cache_resource(show_spinner="Reading database schema…")
def get_schema():
    return load_schema(settings)


@st.cache_resource
def get_generator():
    return make_generator()


if "turns" not in st.session_state:
    st.session_state.turns = []  # the current conversation, oldest first


def new_conversation(first_question=None):
    st.session_state.turns = []
    st.session_state.pending = first_question


# ---------- sidebar ----------
with st.sidebar:
    st.header("✈️ Maintenance Data Assistant")
    st.caption("Ask questions about the fleet, components, inspections and maintenance logs in plain English.")

    try:
        tables = get_schema()
    except pymysql.MySQLError as e:
        st.error(f"Cannot connect to MySQL `{settings.database}` as `{settings.user}`.\n\n{e}")
        st.info("Check .env and run `python db/setup_db.py` first.")
        st.stop()

    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    key_var = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider, "GEMINI_API_KEY")
    if not os.getenv(key_var):
        st.warning(f"{key_var} is not set in .env.")

    st.success(f"Connected to `{settings.database}` (read-only)")
    if settings.allowed_tables is not None:
        st.caption(f"Allowlist on: {len(tables)} table(s) visible.")
        missing = missing_allowlisted(settings, tables)
        if missing:
            st.warning(f"ALLOWED_TABLES lists tables that don't exist: {', '.join(missing)}")
    if len(tables) > settings.selection_threshold:
        st.caption(f"Large schema ({len(tables)} tables): the AI picks relevant tables first.")

    st.button("New conversation", on_click=new_conversation, use_container_width=True, type="primary")

    st.subheader("Try an example")
    for q in EXAMPLE_QUESTIONS:
        st.button(q, on_click=new_conversation, args=(q,), use_container_width=True)

    st.subheader("Schema")
    for t in tables:
        with st.expander(f"{t.name} · ~{t.row_count} rows"):
            if t.comment:
                st.caption(t.comment)
            st.markdown("\n".join(f"- `{c.name}` {c.data_type}" for c in t.columns))

    if st.button("Refresh schema"):
        get_schema.clear()
        st.rerun()


# ---------- main ----------
st.title("Ask the maintenance database")
st.caption(
    "Ask a question, then refine it with follow-ups such as \"only BLR\" or \"sort by cost\". "
    f"The assistant remembers the last {MAX_HISTORY_TURNS} questions in this conversation."
)


def render_answer(ans, key):
    if ans.status == "ok":
        st.markdown(ans.explanation or "Done.")
        if ans.truncated:
            st.warning(
                f"Showing the first {settings.max_rows} rows only; the full result has more. "
                "Narrow the question (a base, an aircraft, a date range) to see everything."
            )
        c1, c2, c3 = st.columns(3)
        c1.metric("Rows", f"{len(ans.frame)}{'+' if ans.truncated else ''}")
        c2.metric("Total time", f"{ans.total_seconds:.1f}s")
        c3.metric("Attempts", ans.attempts)
        if ans.two_step:
            st.caption(f"Tables picked for this question: {', '.join(ans.tables_used)}")
        st.dataframe(ans.frame, use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV", ans.frame.to_csv(index=False).encode(), "results.csv", "text/csv", key=f"dl-{key}",
        )
    elif ans.status == "needs_clarification":
        st.info(f"**Quick question:** {ans.clarifying_question}")
        st.caption("Reply below and I'll write the query.")
    elif ans.status == "not_answerable":
        st.warning(ans.explanation or "This question can't be answered from the maintenance database.")
    elif ans.status == "blocked":
        st.error(ans.message)
    else:
        st.error(ans.message or "Something went wrong.")
    if ans.sql:
        with st.expander("Generated SQL"):
            st.code(ans.sql, language="sql")


for i, turn in enumerate(st.session_state.turns):
    with st.chat_message("user"):
        st.markdown(turn.question)
    with st.chat_message("assistant"):
        render_answer(turn, key=i)

placeholder = "Ask a question…" if not st.session_state.turns else "Ask a follow-up, or click New conversation…"
prompt = st.chat_input(placeholder) or st.session_state.pop("pending", None)

if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Writing and running the query…"):
            answer = answer_question(
                prompt,
                generator=get_generator(),
                tables=tables,
                database=settings.database,
                max_rows=settings.max_rows,
                execute=lambda sql: run_read_only(settings, sql),
                history=st.session_state.turns,
                selection_threshold=settings.selection_threshold,
            )
        render_answer(answer, key=len(st.session_state.turns))
    st.session_state.turns.append(answer)
