"""Schema-aware prompt construction."""
from .db import Table

SYSTEM_RULES = """\
You translate questions from aircraft maintenance and support staff into MySQL 8 queries \
against the database described below. The people asking are not SQL experts, so interpret \
their wording generously, the way an experienced maintenance data analyst would.

Rules:
- Write exactly one read-only SELECT statement (CTEs and UNION are fine). Never write \
INSERT, UPDATE, DELETE, DDL, or anything that changes data or server state.
- If the question asks to change, delete or add data, or cannot be answered from this schema, \
set "answerable" to false, leave "sql" empty, and explain why in "explanation". Do not \
substitute a related but different concept (for example component status when the user asked \
about inspection records): if the table the question is about isn't listed, it isn't answerable.
- Use only the tables and columns listed in the schema. Join tables through the listed \
relationships.
- Match values exactly as they appear in "values" lists and ENUM definitions. For free-text \
names and descriptions, use LIKE '%term%'.
- Prefer human-readable output: include tail_number rather than only aircraft_id, technician \
full_name rather than only technician_id, and give computed columns clear aliases.
- Relative dates ("last month", "next 30 days", "overdue") are relative to CURDATE().
- Add ORDER BY when it makes the result easier to read. Add LIMIT when the user asks for \
"top N"; otherwise the system caps rows automatically.
- "explanation" is one or two plain-English sentences describing what the query returns, \
written for the non-technical person who asked. State any assumption you made (for example \
"this year" taken as calendar year to date).
- Clarifying questions: if the question is ambiguous in a way that would change the answer and \
there is no sensible default (for example "the best technician" could mean most inspections \
passed or most hours logged), set "answerable" to false and put ONE short question in \
"clarifying_question", offering two or three numbered options written as \
"1) ..., 2) ... or 3) ...". If a sensible default exists, do not ask: \
use the default and say so in "explanation". Otherwise leave "clarifying_question" empty.
- Follow-ups: earlier turns of the conversation may be included, each with your previous SQL. \
When the new message refers to them ("only BLR", "sort by cost", "what about last year?") or \
answers your clarifying question, build on the previous query instead of starting over.
"""

EXAMPLES = """\
Examples:
Q: Which aircraft are grounded right now?
SQL: SELECT tail_number, model, operator, base_airport FROM aircraft WHERE status = 'Grounded' ORDER BY tail_number

Q: Components with less than 500 flight hours of life left
SQL: SELECT a.tail_number, c.name, c.serial_number, c.life_limit_hours - c.hours_since_install AS hours_remaining \
FROM components c JOIN aircraft a ON a.aircraft_id = c.aircraft_id \
WHERE c.life_limit_hours IS NOT NULL AND c.life_limit_hours - c.hours_since_install < 500 ORDER BY hours_remaining
"""


def render_schema(tables: list[Table]) -> str:
    """Render tables, columns, comments, keys, sample values and relationships as compact text for the LLM."""
    lines = []
    for t in tables:
        header = f"TABLE {t.name} (~{t.row_count} rows)"
        if t.comment:
            header += f" -- {t.comment}"
        lines.append(header)
        for c in t.columns:
            parts = [f"  {c.name} {c.data_type}"]
            if c.key == "PRI":
                parts.append("PRIMARY KEY")
            elif c.key == "UNI":
                parts.append("UNIQUE")
            if not c.nullable and c.key != "PRI":
                parts.append("NOT NULL")
            line = " ".join(parts)
            if c.comment:
                line += f" -- {c.comment}"
            if c.sample_values:
                line += f" [values: {', '.join(c.sample_values)}]"
            lines.append(line)
        lines.append("")

    relationships = [
        f"  {t.name}.{fk.column} -> {fk.ref_table}.{fk.ref_column}" for t in tables for fk in t.foreign_keys
    ]
    if relationships:
        lines.append("RELATIONSHIPS")
        lines.extend(relationships)
    return "\n".join(lines).strip()


def build_system_prompt(tables: list[Table]) -> str:
    # Stable across requests (no dates or per-request data) so it can be prompt-cached.
    return f"{SYSTEM_RULES}\n<schema>\n{render_schema(tables)}\n</schema>\n\n{EXAMPLES}"


def render_catalog(tables: list[Table]) -> str:
    """Compact one-line-per-table listing used in step 1 of two-step table selection."""
    lines = []
    for t in tables:
        line = f"{t.name}"
        if t.comment:
            line += f" -- {t.comment}"
        line += f" | columns: {', '.join(c.name for c in t.columns)}"
        lines.append(line)
    return "\n".join(lines)


def with_related(tables: list[Table], selected: list[str]) -> list[Table]:
    """Selected tables plus the tables their foreign keys point to, so joins for readable output still work."""
    by_name = {t.name: t for t in tables}
    keep = set(selected)
    for name in selected:
        keep.update(fk.ref_table for fk in by_name[name].foreign_keys)
    return [t for t in tables if t.name in keep]
