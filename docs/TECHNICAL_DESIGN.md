# MX Assistant — Technical Design Document

**Natural-language-to-SQL assistant for aircraft maintenance data**

| | |
|---|---|
| **Document version** | 1.0 |
| **Last updated** | 25 October 2026 |
| **Author** | Diiya Rajeshkumar |
| **Status** | Implemented, running locally on sample data |
| **Repository** | [github.com/diiyark123/nlptosql](https://github.com/diiyark123/nlptosql) |

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Problem statement](#2-problem-statement)
3. [Goals, non-goals and success metrics](#3-goals-non-goals-and-success-metrics)
4. [Users and personas](#4-users-and-personas)
5. [Requirements](#5-requirements)
6. [Solution overview](#6-solution-overview)
7. [Technical architecture](#7-technical-architecture)
8. [System design](#8-system-design)
9. [Data model](#9-data-model)
10. [API specification](#10-api-specification)
11. [Security design](#11-security-design)
12. [Configuration reference](#12-configuration-reference)
13. [Setup and installation](#13-setup-and-installation)
14. [Usage guide](#14-usage-guide)
15. [Testing and quality](#15-testing-and-quality)
16. [Performance and results](#16-performance-and-results)
17. [Operations](#17-operations)
18. [Design decisions and trade-offs](#18-design-decisions-and-trade-offs)
19. [Known limitations](#19-known-limitations)
20. [Future enhancements](#20-future-enhancements)
21. [Glossary](#21-glossary)
22. [Appendix](#22-appendix)

---

## 1. Executive summary

MX Assistant lets maintenance and support staff who don't know SQL query aircraft component, inspection and maintenance-log data in MySQL by typing questions in plain English.

The application reads the live database schema, builds a schema-aware prompt and asks a large language model (LLM) to write a single read-only SQL query. It then validates that query against a strict safety policy and runs it through a database account that can only read. Results come back as a table with a plain-English explanation and the SQL that produced them.

Retrieving data drops from about **15–20 minutes** (a request to a developer) to **3–8 seconds** per question in testing. Developers no longer have to handle routine data requests.

**Key capabilities**

- Plain-English questions, answered from a live MySQL database
- Schema-aware prompting built from `information_schema`, including column descriptions, keys, relationships and sample values
- Four layers of read-only protection: prompt rules, syntax-tree validation, rebuilt SQL and database permissions
- Follow-up questions ("only BLR", "sort by cost") with short-term conversation memory
- Clarifying questions when a request is ambiguous
- Automatic correction of failed queries (one retry, with the error fed back)
- Scaling to large schemas with a table allowlist and two-step table selection
- Choice of LLM provider (Google Gemini, OpenAI or Anthropic Claude), with retries and a fallback model
- Custom web chat interface (FastAPI with an HTML/CSS/JS front end), plus an alternative Streamlit interface
- Audit log of every question with its SQL, outcome, timing and token usage

---

## 2. Problem statement

Maintenance planners, engineers and support staff regularly need operational data:

- which aircraft are grounded
- which parts are near their life limit
- which inspections are overdue
- how much was spent on parts last quarter

The data lives in a relational database, and getting it means writing SQL. Most of these staff can't do that.

**Current process**

1. A staff member asks a developer for the data, by message or ticket.
2. The developer works out what's needed, writes and checks a query, runs it and sends back a spreadsheet.
3. The answer prompts a follow-up question ("now only for BLR"), and the cycle repeats.

**Problems with this process**

| Problem | Impact |
|---|---|
| Each request takes about 15–20 minutes end to end | Slow decisions on safety-relevant data (overdue inspections, life-limited parts) |
| Developers are interrupted for routine queries | Less time for core product work |
| Follow-up questions restart the cycle | Exploration is discouraged |
| Ad-hoc SQL run by hand against production | Risk of accidental writes or heavy queries |

---

## 3. Goals, non-goals and success metrics

### Goals

- **G1.** Non-technical staff can get correct answers to routine data questions without writing SQL.
- **G2.** The system must never modify data, whatever the user asks or whatever the model generates.
- **G3.** Answers arrive in well under a minute.
- **G4.** Users can see and check how each answer was produced (the SQL and an explanation).
- **G5.** The system works against any MySQL schema through configuration, without code changes.

### Non-goals

- Writing, updating or deleting data (explicitly out of scope and blocked).
- Replacing BI dashboards for fixed, recurring reports.
- Answering questions that need data outside the configured database.
- Multi-user authentication and authorization (planned; see [section 20](#20-future-enhancements)).

### Success metrics

| Metric | Target | Current (sample data) |
|---|---|---|
| Time to answer a routine question | < 1 minute | 3–8 s typical; up to ~16 s with two-step selection |
| Data-modifying statements executed | 0 | 0; all write attempts blocked or denied in testing |
| Questions answered without developer help | Most routine requests | All hand-tested routine questions answered |
| Query accuracy | Measured on an evaluation set | Not yet measured (planned) |

---

## 4. Users and personas

| Persona | Typical questions | Needs |
|---|---|---|
| **Maintenance planner** | "Which C-checks are due in the next 30 days?" "Which aircraft are grounded?" | Fast, accurate lists to plan hangar slots |
| **Maintenance engineer** | "Which components are within 10% of their life limit?" "History of work on VT-RAH's main landing gear" | Component-level detail and history |
| **Support / reliability analyst** | "AOG events per ATA chapter in the last 12 months" "Parts cost per aircraft this quarter" | Aggregates and trends, exported to CSV |
| **Developer / administrator** | — | Simple setup, safe by default, easy to point at a real database |

---

## 5. Requirements

### 5.1 Functional requirements

| ID | Requirement | Status |
|---|---|---|
| FR-1 | Accept a question in plain English through a web interface | Done |
| FR-2 | Read the live database schema (tables, columns, types, keys, foreign keys, comments, sample values) | Done |
| FR-3 | Generate exactly one SQL SELECT statement that answers the question | Done |
| FR-4 | Validate every generated query against a read-only safety policy before execution | Done |
| FR-5 | Execute the query and display the results as a table | Done |
| FR-6 | Show a plain-English explanation and the generated SQL with each answer | Done |
| FR-7 | Refuse requests to change data, and questions the schema can't answer, with a reason | Done |
| FR-8 | Support follow-up questions that refine the previous answer | Done |
| FR-9 | Ask a clarifying question when a request is ambiguous and has no sensible default | Done |
| FR-10 | Correct a failed query automatically (invalid SQL or a database error) at least once | Done |
| FR-11 | Cap result size, and tell the user when results are truncated | Done |
| FR-12 | Export results to CSV | Done |
| FR-13 | Restrict the visible tables through an allowlist | Done |
| FR-14 | Handle large schemas by selecting the relevant tables before generating SQL | Done |
| FR-15 | Log every question with its outcome, SQL, timings and token usage | Done |
| FR-16 | Support more than one LLM provider, chosen by configuration | Done |

### 5.2 Non-functional requirements

| ID | Category | Requirement | How it is met |
|---|---|---|---|
| NFR-1 | Security | No data modification is possible | Four layers: prompt rules, AST validator, rebuilt SQL, SELECT-only database user ([section 11](#11-security-design)) |
| NFR-2 | Security | No access to system schemas or other databases | Validator schema checks; `GRANT` limited to one database |
| NFR-3 | Security | Secrets never committed to source control | `.env` ignored by git; `.env.example` holds placeholders only |
| NFR-4 | Performance | Typical answer in < 10 s; hard ceiling well under 1 minute | Measured 3–8 s; 60 s LLM timeout; 15 s database timeout |
| NFR-5 | Reliability | Transient LLM failures don't reach the user | 3 retries with backoff per model, then a fallback model |
| NFR-6 | Reliability | Runaway queries can't overload the database | `max_execution_time` 15 s; LIMIT cap of 500 rows; read-only transaction |
| NFR-7 | Usability | Usable without training by non-technical staff | Example questions, chat interface, explanations, clarifying questions |
| NFR-8 | Transparency | Every answer can be checked | SQL viewer, explanation, audit log |
| NFR-9 | Portability | Works with any MySQL 8 schema | Schema introspection; no hard-coded tables in the pipeline |
| NFR-10 | Maintainability | Core logic is testable without network or database | 63 unit tests using fake LLM and database clients |
| NFR-11 | Scalability | Prompt size stays bounded as the schema grows | Allowlist; two-step selection above 30 tables |

### 5.3 Constraints and assumptions

- **Database:** MySQL 8.0 (tested on 8.0.36). MariaDB is MySQL-compatible but not tested, and its timeout setting differs (`max_statement_time`).
- **Runtime:** Python 3.11+ (developed on 3.13) on macOS. Linux should work without changes.
- **LLM access:** an API key for at least one provider. The free Gemini tier is rate-limited and occasionally overloaded.
- **Data:** the bundled dataset is **synthetic**, generated with a fixed random seed. It is realistic but does not describe real aircraft or people.
- **Deployment:** currently single-user and local. There is no authentication layer yet.

---

## 6. Solution overview

The core idea is that **the LLM only writes SQL; the application decides what runs.**

1. At startup, the app reads the database structure and renders it as compact text.
2. For each question, the app sends the LLM the rules, the schema and the question (plus recent conversation turns for follow-ups).
3. The LLM returns structured JSON: `{answerable, sql, explanation, clarifying_question}`.
4. The app validates the SQL, rebuilds it from the parsed syntax tree, caps the row count, and runs it in a read-only transaction as a SELECT-only user.
5. Results, the explanation and the SQL go back to the user. Everything is logged.

The LLM never connects to the database, never sees credentials, and never executes anything.

---

## 7. Technical architecture

### 7.1 Component diagram

```mermaid
flowchart LR
    subgraph Client["Browser"]
        UI["Web chat UI<br/>web/index.html · app.js · styles.css"]
    end

    subgraph App["Python application"]
        API["FastAPI server<br/>server.py"]
        PIPE["Pipeline<br/>nl2sql/pipeline.py"]
        PROMPT["Prompt builder<br/>nl2sql/prompt.py"]
        LLM["LLM clients<br/>nl2sql/llm.py"]
        VAL["SQL validator<br/>nl2sql/validator.py"]
        DB["DB access<br/>nl2sql/db.py"]
        AUDIT[("Audit log<br/>logs/audit.jsonl")]
        ST["Streamlit UI (alternative)<br/>streamlit_app.py"]
    end

    subgraph Providers["LLM providers"]
        GEM["Google Gemini"]
        OAI["OpenAI"]
        CLA["Anthropic Claude"]
    end

    subgraph MySQL["MySQL 8"]
        IS[("information_schema")]
        MX[("aircraft_mx")]
    end

    UI -- "JSON over HTTP" --> API
    API --> PIPE
    ST --> PIPE
    PIPE --> PROMPT
    PIPE --> LLM
    PIPE --> VAL
    PIPE --> DB
    PIPE --> AUDIT
    LLM -- "HTTPS, structured JSON" --> GEM
    LLM -.-> OAI
    LLM -.-> CLA
    DB -- "schema read at startup" --> IS
    DB -- "SELECT as nl2sql_reader<br/>READ ONLY transaction" --> MX
```

### 7.2 Technology stack

| Layer | Technology | Why |
|---|---|---|
| Front end | HTML, CSS and vanilla JavaScript (no build step) | Full control over the design; zero front-end tooling; a fast, light page |
| Web server | FastAPI + Uvicorn | Typed request models, automatic validation, minimal boilerplate |
| Alternative UI | Streamlit | Quick internal tool; same pipeline underneath |
| LLM (default) | Google Gemini `gemini-3.5-flash`, fallback `gemini-3.5-flash-lite` | Fast, free tier available, JSON-schema-constrained output |
| LLM (optional) | OpenAI `gpt-4.1`; Anthropic `claude-opus-5-5` | Provider choice by configuration |
| SQL parsing | sqlglot (MySQL dialect) | Real syntax tree instead of fragile regular expressions; can regenerate SQL from the tree |
| Database | MySQL 8.0 | Target platform |
| DB driver | PyMySQL | Pure Python, no native dependencies |
| Data handling | pandas | Tabular results, CSV export |
| Configuration | python-dotenv | `.env`-based configuration, kept out of git |
| Testing | pytest | Unit tests with fake LLM and database clients |

### 7.3 Repository structure

```
nlptosql/
├── server.py                # FastAPI backend: /api/status, /api/schema, /api/ask, serves web/
├── streamlit_app.py         # Alternative Streamlit UI (same pipeline)
├── nl2sql/
│   ├── config.py            # Settings from .env (connection, limits, allowlist, threshold)
│   ├── db.py                # Schema introspection, read-only query execution
│   ├── prompt.py            # System rules, schema rendering, table catalog, FK expansion
│   ├── llm.py               # Gemini / OpenAI / Claude clients, structured output, retries, fallback
│   ├── validator.py         # sqlglot-based read-only validation and LIMIT capping
│   └── pipeline.py          # Orchestration: selection, follow-ups, clarification, retry, audit
├── web/
│   ├── index.html           # Page shell
│   ├── styles.css           # Design tokens, light and dark themes, responsive layout
│   └── app.js               # Chat, results table, SQL viewer, CSV export
├── db/
│   ├── schema.sql           # 5-table maintenance schema with descriptive column comments
│   ├── setup_db.py          # Creates database, loads sample data, creates read-only user
│   └── show_databases.py    # Lists databases, tables and columns
├── tests/                   # 63 pytest tests (no network or database needed)
├── docs/
│   ├── TECHNICAL_DESIGN.md  # This document
│   └── images/              # Screenshots
├── logs/                    # Runtime audit log (git-ignored)
├── .env.example             # Configuration template (placeholders only)
├── requirements.txt
└── README.md
```

---

## 8. System design

### 8.1 Request lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant W as Web UI
    participant S as FastAPI
    participant P as Pipeline
    participant L as LLM
    participant V as Validator
    participant D as MySQL (read-only)

    U->>W: "Show overdue inspections at Bangalore"
    W->>S: POST /api/ask {question, conversation_id}
    S->>P: answer_question(question, history)
    opt Schema larger than threshold (two-step mode)
        P->>L: Table catalog + question
        L-->>P: {"tables": ["inspections", ...]}
    end
    P->>L: Rules + schema + history + question
    L-->>P: {answerable, sql, explanation, clarifying_question}
    alt Not answerable or needs clarification
        P-->>S: status + explanation / clarifying question
    else SQL proposed
        P->>V: validate(sql)
        alt Unsafe (write, DDL, dangerous function)
            V-->>P: UnsafeQueryError
            P-->>S: status "blocked"
        else Invalid (syntax, unknown table)
            V-->>P: InvalidQueryError
            P->>L: Error message, "please correct" (one retry)
        else Safe
            V-->>P: rebuilt SQL with LIMIT
            P->>D: START TRANSACTION READ ONLY; SELECT ...
            D-->>P: rows (or error, which triggers one retry)
            P-->>S: status "ok", rows, SQL, explanation
        end
    end
    P->>P: Append to audit log
    S-->>W: JSON answer
    W-->>U: Explanation, table, SQL, CSV
```

### 8.2 Schema introspection

`nl2sql/db.py → load_schema()` runs once at startup and caches the result. A refresh endpoint reloads it.

| Source (`information_schema`) | What is extracted | Why it matters |
|---|---|---|
| `TABLES` | name, comment, approximate row count | Context on what each table holds |
| `COLUMNS` | name, full type (including ENUM values), nullability, key, comment | Exact column names; business rules in comments |
| `KEY_COLUMN_USAGE` | foreign keys | Correct join paths |
| `SELECT DISTINCT` on text columns | up to 12 real values for low-cardinality columns | The model uses `'BLR'`, not `'Bangalore'` |

**Business rules live in column comments.** For example, `completed_date` carries: *"NULL = not yet completed. Overdue = completed_date IS NULL AND scheduled_date < CURDATE()"*. The schema itself documents the domain, and pointing the app at a different database needs no prompt changes.

**Prompt size:** the bundled schema renders to about 1,000 tokens (5 tables, 46 columns, ~210 tokens per table). The full system prompt is about 1,500 tokens.

### 8.3 Prompt design

The system prompt has three parts and contains nothing that changes per request, so providers can cache it.

1. **Rules**
   - Write exactly one read-only SELECT; never write or change data.
   - Mark data-change requests and out-of-scope questions as not answerable, with a reason. Don't substitute a different concept when the needed table is missing.
   - Use only the listed tables and columns, joined through the listed relationships.
   - Match listed values exactly; use `LIKE '%term%'` for free text.
   - Prefer readable output (tail number, technician name) over raw IDs.
   - Relative dates are measured from `CURDATE()`.
   - Ask one clarifying question (with numbered options) only when there is no sensible default. Otherwise use the default and state it.
   - Build on the previous query for follow-ups.
2. **Schema**, inside `<schema>` tags: the rendered tables and relationships.
3. **Two worked examples:** a filter query and a remaining-life join.

The **user turn** carries today's date and the question. The date is kept out of the system prompt so the system prompt stays cacheable.

### 8.4 LLM integration

All providers implement one method, `_complete_json(system_prompt, messages, schema)`, and return parsed JSON. The shared base class builds the two operations the pipeline needs on top of it:

| Operation | JSON schema | Used for |
|---|---|---|
| `propose()` | `{answerable: bool, sql: string, explanation: string, clarifying_question: string}` | Writing SQL |
| `select_tables()` | `{tables: [enum of real table names]}` | Two-step selection on large schemas |

**Structured output** is enforced by each provider's JSON-schema mode, so there is no free-text parsing. In table selection, the `enum` stops the model inventing table names.

**Gemini resilience** (`GeminiSQLGenerator._generate`):

```mermaid
flowchart LR
    A[Request] --> B{gemini-3.5-flash}
    B -- ok --> Z[Return]
    B -- "503 / 500 / 504 / 429 / network" --> C[Wait 1 s, retry] --> D[Wait 3 s, retry]
    D -- still failing --> E{gemini-3.5-flash-lite<br/>3 attempts}
    E -- ok --> Z
    E -- still failing --> F[Clear error to user]
    B -- "400 / 401 / 403" --> F
```

Requests time out after 60 s. Errors are mapped to plain-English messages: invalid key, quota reached, provider overloaded, network unreachable.

### 8.5 Two-step table selection (large schemas)

When more tables are visible than `SCHEMA_SELECTION_THRESHOLD` (default 30):

1. **Select.** The LLM receives a compact catalog (`name -- description | columns: ...`, about 15–30 tokens per table) and returns the tables the question needs.
2. **Expand.** The app adds every table the selected ones reference through foreign keys (for example `aircraft` for tail numbers).
3. **Generate.** The normal prompt is built with full detail for only those tables.

With 500 tables this cuts the per-question prompt from about 100K tokens to about 10K. For follow-ups, the previous turns' questions are included in the selection context, and their tables are kept.

### 8.6 Conversation memory (follow-ups)

- The server keeps each conversation's answers in memory, keyed by `conversation_id` (up to 200 conversations; the oldest are dropped first).
- The last **3** successful turns are replayed to the LLM as user/assistant message pairs. Each pair contains the earlier question, the earlier JSON answer (including its SQL) and a note on what it returned (row count and column names).
- Failed turns (errors, blocked queries) are not replayed.
- The conversation is append-only, which keeps provider-side caching effective.

### 8.7 Clarifying questions

If a question is ambiguous and has no sensible default, the model returns `answerable: false` with a `clarifying_question` offering numbered options. The UI turns `1) …, 2) …, 3) …` into buttons. The user's reply is treated as a follow-up, so the original question is answered with the chosen meaning. When a sensible default exists ("this year" means calendar year to date), the model uses it and says so in the explanation instead of asking.

### 8.8 Validation and execution

`nl2sql/validator.py → validate_sql()`:

| Check | Result if it fails |
|---|---|
| Empty query | Invalid (retry) |
| Contains `/*! … */` (MySQL executable comment) | Unsafe (blocked) |
| Contains `INTO OUTFILE`, `INTO DUMPFILE` or `INTO @var` | Unsafe |
| SQL can't be parsed | Invalid (retry) |
| More than one statement | Unsafe |
| Root isn't SELECT, UNION, INTERSECT or EXCEPT | Unsafe |
| Any INSERT, UPDATE, DELETE, MERGE, DROP, CREATE, ALTER, TRUNCATE, GRANT, SET, USE, transaction control or `FOR UPDATE` anywhere in the tree | Unsafe |
| Calls SLEEP, BENCHMARK, LOAD_FILE, GET_LOCK or similar | Unsafe |
| References `mysql`, `information_schema`, `performance_schema`, `sys` or another database | Unsafe |
| References a table that isn't visible (CTE names excepted) | Invalid (retry) |

On success, the SQL is **regenerated from the validated syntax tree** (so comments the validator didn't evaluate can't survive) and a `LIMIT` is added or lowered to `MAX_RESULT_ROWS`.

**Execution** (`db.run_read_only`):

```sql
SET SESSION max_execution_time = 15000;   -- server-side timeout (ms)
START TRANSACTION READ ONLY;
<validated SELECT ... LIMIT 501>          -- one extra row to detect truncation
ROLLBACK;
```

If 501 rows come back, the user sees 500 and a truncation warning. The SQL shown to the user keeps `LIMIT 500`.

### 8.9 Self-correction loop

```mermaid
stateDiagram-v2
    [*] --> Generate
    Generate --> NotAnswerable: answerable = false
    Generate --> Validate: SQL proposed
    Validate --> Blocked: unsafe
    Validate --> Execute: safe
    Validate --> Retry: invalid
    Execute --> Done: rows returned
    Execute --> Retry: MySQL error
    Retry --> Generate: attempt 2 (error fed back)
    Retry --> Failed: attempts exhausted
    NotAnswerable --> [*]
    Blocked --> [*]
    Done --> [*]
    Failed --> [*]
```

Unsafe queries are **never retried**. Retrying would give the model another chance to get a write past the validator.

### 8.10 Audit logging

Every question appends one JSON line to `logs/audit.jsonl`:

- question and status
- explanation, SQL and message
- clarifying question
- whether results were truncated, and the number of attempts
- tables used, two-step flag, follow-up flag
- LLM, database and total seconds
- input and output tokens
- row count and timestamp

The log supports usage analysis, debugging, measuring time saved, and building an evaluation set from real questions. See [section 22.2](#222-sample-audit-record) for an example.

---

## 9. Data model

The bundled `aircraft_mx` database models a small airline maintenance operation. All data is **synthetic**, generated deterministically by `db/setup_db.py` (seed 42).

```mermaid
erDiagram
    AIRCRAFT ||--o{ COMPONENTS : "has installed"
    AIRCRAFT ||--o{ INSPECTIONS : "undergoes"
    AIRCRAFT ||--o{ MAINTENANCE_LOGS : "receives"
    COMPONENTS ||--o{ INSPECTIONS : "may be target of"
    COMPONENTS ||--o{ MAINTENANCE_LOGS : "may be worked on in"
    TECHNICIANS ||--o{ INSPECTIONS : "signs off"
    TECHNICIANS ||--o{ MAINTENANCE_LOGS : "performs"

    AIRCRAFT {
        int aircraft_id PK
        varchar tail_number UK
        varchar model
        varchar manufacturer
        smallint year_of_manufacture
        varchar operator
        char base_airport
        int total_flight_hours
        enum status
    }
    COMPONENTS {
        int component_id PK
        int aircraft_id FK
        varchar part_number
        varchar serial_number UK
        varchar name
        enum category
        int hours_since_install
        int life_limit_hours
        enum status
    }
    INSPECTIONS {
        int inspection_id PK
        int aircraft_id FK
        int component_id FK
        int technician_id FK
        enum inspection_type
        date scheduled_date
        date completed_date
        enum result
        text findings
    }
    MAINTENANCE_LOGS {
        int log_id PK
        int aircraft_id FK
        int component_id FK
        int technician_id FK
        date log_date
        enum action_type
        varchar defect_code
        decimal labor_hours
        decimal parts_cost
        boolean is_aog
    }
    TECHNICIANS {
        int technician_id PK
        varchar full_name
        varchar license_number UK
        varchar specialization
        char base_airport
        date hire_date
    }
```

| Table | Rows | Contents | Key business rules (column comments) |
|---|---|---|---|
| `aircraft` | 25 | Fleet register: A320neo, A321neo, B737-800, B737 MAX 8, ATR 72-600, E175; Indian operators and bases | Status: Active, In Maintenance, Grounded, Retired |
| `technicians` | 20 | Licensed engineers (AME licence numbers) by specialization and base | — |
| `components` | 290 | Life-limited and rotable parts (engines, landing gear, avionics, APU, …) | Remaining life = `life_limit_hours - hours_since_install`; NULL limit = on-condition part; NULL `aircraft_id` = in stores |
| `inspections` | 700 | A/B/C/D checks, visual, NDT and borescope inspections; 52 overdue | Overdue = `completed_date IS NULL AND scheduled_date < CURDATE()` |
| `maintenance_logs` | 1,600 | Repairs, replacements, overhauls, etc. over two years | ATA-chapter defect codes; `parts_cost` in USD; `is_aog` = aircraft grounded for the work |

---

## 10. API specification

Base URL: `http://localhost:8000`. All responses are JSON.

### `GET /api/status`

Returns connection state, model and settings for the UI.

```json
{
  "db": {"connected": true, "database": "aircraft_mx", "user": "nl2sql_reader", "tables": 5},
  "llm": {"provider": "gemini", "model": "gemini-3.5-flash"},
  "max_rows": 500,
  "history_turns": 3,
  "allowlist": null,
  "allowlist_missing": [],
  "two_step": false,
  "examples": ["Which aircraft are currently grounded or in maintenance?", "..."]
}
```

### `GET /api/schema`

Lists the visible tables with comments, approximate row counts, columns (name, type, key, comment) and foreign keys.

### `POST /api/schema/refresh`

Reloads the schema from MySQL. Returns `{"tables": 5}`.

### `POST /api/ask`

**Request**

```json
{"question": "only BLR or DEL", "conversation_id": "4e68a76b..."}
```

`conversation_id` is optional. Omit it to start a new conversation; send the returned value back for follow-ups.

**Response**

```json
{
  "conversation_id": "4e68a76b...",
  "answer": {
    "question": "only BLR or DEL",
    "status": "ok",
    "explanation": "This filters the previous list to aircraft based in BLR or DEL.",
    "sql": "SELECT tail_number, model, ... WHERE status IN ('Grounded', 'In Maintenance') AND base_airport IN ('BLR', 'DEL') ... LIMIT 500",
    "message": "",
    "clarifying_question": "",
    "truncated": false,
    "attempts": 1,
    "tables_used": ["aircraft", "components", "inspections", "maintenance_logs", "technicians"],
    "two_step": false,
    "is_follow_up": true,
    "total_seconds": 6.9,
    "llm_seconds": 6.88,
    "db_seconds": 0.004,
    "columns": ["tail_number", "model", "operator", "base_airport", "status"],
    "rows": [["VT-RAF", "B737 MAX 8", "IndiGo", "BLR", "Grounded"]]
  }
}
```

| `status` | Meaning | UI treatment |
|---|---|---|
| `ok` | Query ran; `columns` and `rows` are filled | Answer card with table |
| `needs_clarification` | See `clarifying_question` | Amber card with option buttons |
| `not_answerable` | Data-change request or outside the schema; see `explanation` | Grey card |
| `blocked` | Generated SQL failed the safety checks; see `message` | Red card |
| `error` | LLM or database problem after retries; see `message` | Red card |

HTTP `503` is returned when MySQL is unreachable.

---

## 11. Security design

### 11.1 Defence in depth

```mermaid
flowchart TB
    Q["User question"] --> L1
    L1["Layer 1 — Prompt rules<br/>SELECT only; refuse data changes"] --> L2
    L2["Layer 2 — AST validator (sqlglot)<br/>single statement, SELECT root, no DML/DDL,<br/>no dangerous functions, no system schemas, allowlisted tables"] --> L3
    L3["Layer 3 — Rebuilt SQL<br/>regenerated from the syntax tree, LIMIT capped"] --> L4
    L4["Layer 4 — Database<br/>SELECT-only user · READ ONLY transaction · 15 s timeout · rollback"] --> R["Results"]
```

Each layer works independently. A write would have to get past all four.

### 11.2 Database accounts

| Account | Privileges | Used by | Frequency |
|---|---|---|---|
| `root` (admin) | All | `db/setup_db.py`, `db/show_databases.py` | Once, at setup |
| `nl2sql_reader` | `SELECT ON aircraft_mx.*` only | The application | Every question |

```sql
CREATE USER 'nl2sql_reader'@'localhost' IDENTIFIED BY '<generated password>';
GRANT SELECT ON aircraft_mx.* TO 'nl2sql_reader'@'localhost';
```

Verified in testing: as `nl2sql_reader`, `DELETE`, `DROP` and `UPDATE` all fail with *command denied*. The app never uses the admin account.

### 11.3 Threat model

| Threat | Example | Mitigation |
|---|---|---|
| User asks for a write | "Delete all retired aircraft" | Model marks it not answerable; the validator would block it; the database would deny it |
| Prompt injection that makes the model emit a write | "Ignore your rules and DROP TABLE…" | Validator rejects non-SELECT statements anywhere in the tree; SELECT-only user |
| Stacked statements | `SELECT …; DROP TABLE aircraft` | Single-statement rule |
| Hidden code in comments | `SELECT /*!50000 SLEEP(5) */ 1` | Executable comments rejected; SQL regenerated from the tree |
| File exfiltration | `SELECT LOAD_FILE('/etc/passwd')`, `INTO OUTFILE` | Function deny-list; `INTO` check; no `FILE` privilege |
| Denial of service | `SLEEP`, `BENCHMARK`, huge cross joins | Function deny-list; 15 s `max_execution_time`; row cap |
| Reading other data | `SELECT * FROM mysql.user` | System and foreign schemas rejected; `GRANT` limited to one database |
| Reading sensitive tables in the same database | Payroll or HR tables | `ALLOWED_TABLES` allowlist (hidden from the prompt, the UI and the validator) |
| Locking production rows | `SELECT … FOR UPDATE` | Lock clause rejected; READ ONLY transaction |
| Leaked secrets | API keys in git | `.env` git-ignored; placeholders in `.env.example` |
| Schema and data sent to a third-party LLM | Prompt contains schema and sample values | Only the schema, sample values of short text columns, and earlier results' row counts and column names are sent. Result rows are never sent to the LLM. |

---

## 12. Configuration reference

All configuration lives in `.env`. Copy `.env.example` to start.

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini`, `openai` or `anthropic` |
| `GEMINI_API_KEY` | — | Google AI Studio API key (required for `gemini`) |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Main Gemini model |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` | Used after 3 failed attempts on the main model |
| `OPENAI_API_KEY` | — | Required for `openai` |
| `OPENAI_MODEL` | `gpt-4.1` | OpenAI model |
| `ANTHROPIC_API_KEY` | — | Required for `anthropic` (model `claude-opus-5-5`) |
| `MYSQL_HOST` | `127.0.0.1` | MySQL host |
| `MYSQL_PORT` | `3306` | MySQL port |
| `MYSQL_DATABASE` | `aircraft_mx` | Database the assistant queries |
| `MYSQL_ADMIN_USER` | `root` | Admin account, used only by setup scripts |
| `MYSQL_ADMIN_PASSWORD` | — | Admin password, used only by setup scripts |
| `MYSQL_READONLY_USER` | `nl2sql_reader` | Account the app connects with |
| `MYSQL_READONLY_PASSWORD` | — | Its password; `setup_db.py` creates the user with it |
| `MAX_RESULT_ROWS` | `500` | Row cap per query; more rows trigger a truncation warning |
| `QUERY_TIMEOUT_MS` | `15000` | MySQL `max_execution_time` per query |
| `ALLOWED_TABLES` | *(empty = all)* | Comma-separated list of the only tables the assistant may see |
| `SCHEMA_SELECTION_THRESHOLD` | `30` | Above this many visible tables, two-step table selection is used |

---

## 13. Setup and installation

### 13.1 Prerequisites

- Python 3.11 or newer
- MySQL 8.0 running locally or reachable over the network, with admin access for the one-time setup
- An API key for Gemini ([Google AI Studio](https://aistudio.google.com/)), OpenAI or Anthropic

### 13.2 Install

```bash
git clone https://github.com/diiyark123/nlptosql.git
cd nlptosql
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### 13.3 Configure

Edit `.env`:

1. Set `LLM_PROVIDER` and that provider's API key.
2. Set `MYSQL_ADMIN_PASSWORD` to your MySQL root (or other admin) password.
3. Set `MYSQL_READONLY_PASSWORD` to any strong password for the app's read-only user. For example, generate one with: `python -c "import secrets; print(secrets.token_urlsafe(18))"`.

### 13.4 Create the database

```bash
python db/setup_db.py              # create database, tables, sample data and read-only user
python db/setup_db.py --reset      # drop and rebuild
python db/show_databases.py aircraft_mx   # check: list databases, tables and columns
```

Expected output:

```
Loaded 25 aircraft, 20 technicians, 290 components, 700 inspections, 1600 maintenance logs into 'aircraft_mx'.
Read-only user 'nl2sql_reader' has SELECT on 'aircraft_mx'.
```

### 13.5 Run

```bash
uvicorn server:app --port 8000     # web UI at http://localhost:8000
# alternative:
streamlit run streamlit_app.py     # Streamlit UI at http://localhost:8501
```

### 13.6 Verify read-only access (optional)

```bash
mysql -u nl2sql_reader -p aircraft_mx
```

```sql
SELECT COUNT(*) FROM aircraft;   -- 25
DELETE FROM aircraft;            -- ERROR 1142: DELETE command denied
```

### 13.7 Using your own database

1. Point `MYSQL_DATABASE` at your schema.
2. Create a read-only user and `GRANT SELECT ON yourdb.* TO ...` (or ask your DBA for one).
3. Optionally set `ALLOWED_TABLES` to the tables staff need.
4. Add `COMMENT`s to important tables and columns (units, codes, business rules such as "overdue means …"). This is the single most effective way to improve accuracy.

### 13.8 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Access denied for user 'root'` | Wrong admin password | Check `MYSQL_ADMIN_PASSWORD` |
| `Can't connect to MySQL server` | MySQL not running | Start MySQL (macOS: System Settings → MySQL) |
| `Authentication plugin 'caching_sha2_password' cannot be loaded` | A MariaDB client connecting to MySQL 8 | Use MySQL's own client (for example `/usr/local/mysql/bin/mysql`) |
| `Gemini is overloaded (503)` after retries | Provider-side overload | Wait a minute; consider a paid tier |
| `Gemini quota or rate limit reached` | Free-tier per-minute limit | Wait a minute between bursts |
| Model returns a 404 "no longer available" | Model retired for new users | Set `GEMINI_MODEL` to a current model |
| Sidebar warns "ALLOWED_TABLES lists tables that don't exist" | Typo in the allowlist | Fix the table names |

---

## 14. Usage guide

| Try | What happens |
|---|---|
| "Which aircraft are currently grounded or in maintenance?" | Simple filter on `aircraft.status` |
| "List components within 10% of their life limit, closest first" | Remaining-life calculation joined to tail numbers |
| "Show overdue inspections at Bangalore" | Uses the overdue rule from the column comment; maps Bangalore to `BLR` |
| "Total parts cost and labour hours per aircraft in the last 6 months, top 5" | Aggregation with a relative date |
| Follow-up: "only BLR or DEL", then "sort by flight hours" | Refines the previous SQL instead of starting over |
| "Who is our best technician?" | Clarifying question with three option buttons |
| "List every maintenance log entry" | 500 rows plus a truncation warning (1,600 exist) |
| "Delete all retired aircraft" | Refused: read-only |
| "What's the weather in Chennai?" | Refused: not in the database |

**Tips:** click a column header to sort; use **Download CSV** for spreadsheets; open **View SQL** to check the logic; start a **New conversation** when changing topic.

---

## 15. Testing and quality

### 15.1 Automated tests

63 tests run in about 2.5 seconds and need **no network, API key or database**. The LLM and the database are replaced with fakes.

| File | Tests | Covers |
|---|---|---|
| `tests/test_validator.py` | 36 | 7 valid read queries allowed; 22 attack patterns blocked (DML, DDL, GRANT, SET, CALL, stacked statements, INTO OUTFILE / @var, FOR UPDATE, SLEEP, BENCHMARK, LOAD_FILE, system schemas, other databases, executable comments, SLEEP in a subquery); unknown tables marked fixable; LIMIT added, kept or capped; comments stripped; CTEs allowed |
| `tests/test_pipeline.py` | 23 | Schema rendering; cacheable system prompt; happy path; writes blocked without retry; not answerable; self-correction after an unknown table and after a MySQL error; API errors; audit log; allowlist parsing; one-step and two-step modes; FK expansion; prompt contains only selected tables; truncation detection; clarifying questions; follow-up replay; history cap; failed turns excluded |
| `tests/test_gemini_retry.py` | 4 | Retry on 503 then success; fallback to the lite model; clear error after 6 failures; bad requests not retried |

```bash
pytest            # 63 passed
```

### 15.2 Live end-to-end checks

Run against the real MySQL database with Gemini:

| Scenario | Result |
|---|---|
| Overdue inspections at Bangalore | Correct join and overdue rule; 14 rows |
| Top 5 aircraft by parts cost, last 6 months | Correct aggregation; VT-RAH highest |
| Components within 10% of life limit | Correct remaining-life calculation |
| Technicians with most failed inspections (two-step mode forced) | Selected `inspections` and `technicians`; 12 rows |
| Allowlist hides `inspections`; ask about overdue inspections | Correctly *not answerable* |
| Follow-up chain (grounded → only BLR/DEL → sort by hours) | Each step built on the previous SQL |
| "Best technician" → clarifying question → reply | Three options offered; reply produced the right query |
| Result over 500 rows | 500 shown with a truncation warning |
| Delete request; weather question | Both refused |
| Read-only user attempting DELETE, DROP, UPDATE directly | All denied by MySQL |

### 15.3 UI checks

The web UI was checked in a headless browser (Playwright/Chromium): empty state, loading steps, answer with SQL, follow-up, clarifying question with option buttons, refused request, light and dark themes. No console errors.

---

## 16. Performance and results

| Measure | Value |
|---|---|
| Typical end-to-end answer | 3–8 s |
| Two-step mode (two LLM calls) | ~13–16 s |
| Database execution | < 50 ms on the sample data |
| Prompt size (5 tables) | ~1,500 input tokens per question; ~100 output tokens |
| Retrieval time vs. manual process | ~15–20 min → under 10 s |

Most of the latency is LLM time. Database time is negligible at this scale.

---

## 17. Operations

| Task | How |
|---|---|
| Start | `uvicorn server:app --port 8000` (inside the virtual environment) |
| Reload schema after a DDL change | Sidebar refresh button, or `POST /api/schema/refresh` |
| Change model or provider | Edit `.env` and restart |
| Usage review | `logs/audit.jsonl`, one JSON record per question |
| Rebuild sample data | `python db/setup_db.py --reset` |
| Rotate the read-only password | Change `MYSQL_READONLY_PASSWORD`, run `python db/setup_db.py` (it updates the user), restart |

---

## 18. Design decisions and trade-offs

| Decision | Alternatives considered | Rationale |
|---|---|---|
| Parse SQL with sqlglot and check the syntax tree | Keyword regular expressions | Regexes miss nested statements and comments; a tree check is exact, and lets the SQL be rebuilt cleanly |
| Rebuild the SQL from the validated tree | Run the model's text as-is | Removes any content the validator didn't evaluate (comments, executable comments) |
| Structured JSON output | Pull SQL out of free-text answers | No parsing ambiguity; an explicit `answerable` and `clarifying_question` |
| Read the schema at startup and cache it | Hard-coded schema in the prompt; read per request | Works with any database; no per-request cost; manual refresh is enough because schema changes are rare |
| Business rules in column comments | Rules in the prompt | Keeps domain knowledge with the data; portable across databases |
| Never retry unsafe queries | Retry everything | A retry is another chance to slip a write through |
| Send only schema and row counts to the LLM, never result rows | Send results for summarizing | Less data leaves the network; smaller prompts |
| Separate SELECT-only database user | Rely on validation alone | Independent last line of defence |
| Vanilla JavaScript front end | React or another framework | No build step; small, fast page; full design control |
| In-memory conversation store | Database or Redis | Simple for a single instance; can be swapped out when deploying |
| Provider-neutral LLM interface | Single provider | Free-tier outages and pricing differences make switching useful |

---

## 19. Known limitations

| Limitation | Impact | Current mitigation |
|---|---|---|
| Free Gemini tier reliability | Occasional 503 or 429 errors | Retries, fallback model, clear messages |
| Accuracy not formally measured | No accuracy figure yet | SQL shown for checking; about 15 hand-tested scenarios |
| Synthetic data only | Not yet tried on a production maintenance database | Works with any MySQL schema through configuration |
| No authentication or roles | Anyone who can reach the app can query visible tables | Runs on localhost; allowlist; read-only user |
| In-memory conversations | Lost on restart; single instance only | Fine for local, single-user use |
| Schema read at startup | New tables need a refresh | Refresh button and endpoint |
| Secrets in a local `.env` file | Plain-text keys on disk | File is user-only and git-ignored |

---

## 20. Future enhancements

| Priority | Enhancement | Value | Effort |
|---|---|---|---|
| **P1** | **Evaluation set:** 30–50 questions with known correct answers, scored by comparing query results | A real accuracy figure; safe prompt and model changes | Medium |
| **P1** | **Authentication and per-role table access** (company SSO; allowlist per role) | Planners, engineers and support each see only their own tables | Medium |
| P2 | Answer feedback (thumbs up/down) feeding the evaluation set | Continuous improvement from real usage | Small |
| P2 | Cross-provider fallback (Gemini, then Claude or OpenAI) | Removes single-provider outages | Small |
| P2 | Row-level filters (for example by base airport) | A BLR engineer sees only BLR aircraft | Medium |
| P3 | Automatic charts for trends and totals | Faster reading of cost and AOG trends | Medium |
| P3 | Saved questions and scheduled reports | "Every Monday, email overdue inspections" | Medium |
| P3 | Embedding-based schema retrieval | Scales to thousands of tables with one LLM call | Medium |
| P3 | Docker image, CI pipeline, persistent conversation store | Deployment to an internal server | Medium |

---

## 21. Glossary

| Term | Meaning |
|---|---|
| **AOG** | Aircraft On Ground — an aircraft grounded and unable to fly until maintenance is done |
| **ATA chapter** | Industry-standard numbering of aircraft systems (for example ATA 32 = landing gear, ATA 72 = engine) |
| **A/B/C/D check** | Scheduled maintenance checks of increasing depth and interval |
| **AME** | Aircraft Maintenance Engineer (licensed technician) |
| **Borescope** | Visual inspection of engine internals with an optical probe |
| **Life-limited part** | A component that must be removed after a set number of flight hours |
| **MRO** | Maintenance, Repair and Overhaul |
| **NDT** | Non-Destructive Testing (ultrasound, eddy current, etc.) |
| **On-condition part** | A component replaced based on inspection results rather than a fixed life limit |
| **Tail number** | An aircraft's registration (for example VT-RAH) |
| **AST** | Abstract Syntax Tree — the parsed structure of a SQL statement |
| **NL-to-SQL** | Turning a natural-language question into a SQL query |
| **Two-step selection** | First choosing the relevant tables, then generating SQL using only those |

---

## 22. Appendix

### 22.1 Rendered schema excerpt (as sent to the LLM)

```
TABLE aircraft (~25 rows) -- Fleet register: one row per aircraft
  aircraft_id int PRIMARY KEY
  tail_number varchar(10) UNIQUE NOT NULL -- Aircraft registration, e.g. VT-RMA
  model varchar(40) NOT NULL -- Aircraft type, e.g. A320neo, B737-800, ATR 72-600 [values: A320neo, A321neo, ATR 72-600, B737 MAX 8, B737-800, E175]
  base_airport char(3) NOT NULL -- IATA code of home maintenance base [values: BLR, BOM, CCU, DEL, HYD, MAA]
  status enum('Active','In Maintenance','Grounded','Retired') NOT NULL
  ...
RELATIONSHIPS
  components.aircraft_id -> aircraft.aircraft_id
  inspections.technician_id -> technicians.technician_id
  ...
```

### 22.2 Sample audit record

```json
{
  "question": "Who is our best technician?",
  "status": "needs_clarification",
  "clarifying_question": "How would you like to measure the best technician? 1) Most maintenance logs/tasks completed, 2) Most inspections passed, or 3) Most total labor hours logged?",
  "attempts": 1,
  "two_step": false,
  "is_follow_up": false,
  "llm_seconds": 6.34,
  "total_seconds": 6.34,
  "tokens": {"input": 1932, "output": 95},
  "rows": null,
  "timestamp": "2026-10-25T17:05:22"
}
```
