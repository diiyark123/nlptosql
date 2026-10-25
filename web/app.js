// MX Assistant front end: chat, results table, SQL viewer. No build step, no framework.
(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    app: $("app"), messages: $("messages"), empty: $("empty"), input: $("input"), send: $("sendBtn"),
    composer: $("composer"), title: $("convTitle"), memory: $("memoryChip"), status: $("status"),
    sideExamples: $("sideExamples"), grid: $("exampleGrid"), schema: $("schemaList"), dbName: $("dbName"),
    themeBtn: $("themeBtn"), newChat: $("newChat"), refresh: $("refreshSchema"), menu: $("menuBtn"), scrim: $("scrim"),
  };

  const state = { conversationId: null, busy: false, turns: 0, maxRows: 500, historyTurns: 3, thread: null };

  // ---------- icons ----------
  const icon = (path, size = 16, sw = 2) =>
    `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  const I = {
    plane: '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>',
    gauge: '<path d="M12 14l4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>',
    alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    coins: '<circle cx="8" cy="8" r="6"/><path d="M18.1 10.4A6 6 0 1 1 10.3 18"/><path d="M7 6h1v4"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 12 0v1"/>',
    wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z"/>',
    chevron: '<path d="m9 18 6-6-6-6"/>',
    code: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
    copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    x: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    rows: '<path d="M3 3h18v18H3zM3 9h18M3 15h18"/>',
    redo: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  };
  const EXAMPLE_ICONS = [I.plane, I.gauge, I.calendar, I.coins, I.user, I.alert];

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // ---------- theme ----------
  const THEMES = ["auto", "light", "dark"];
  function applyTheme(t) {
    if (t === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    try { localStorage.setItem("mx-theme", t === "auto" ? "" : t); } catch (e) {}
    els.themeBtn.innerHTML = icon(t === "light" ? I.sun : t === "dark" ? I.moon : I.auto, 17);
    els.themeBtn.title = `Theme: ${t} (click to change)`;
  }
  let theme = document.documentElement.dataset.theme || "auto";
  applyTheme(theme);
  els.themeBtn.onclick = () => applyTheme((theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]));

  // ---------- sidebar ----------
  els.menu.onclick = () => els.app.classList.add("nav-open");
  els.scrim.onclick = () => els.app.classList.remove("nav-open");

  async function loadStatus() {
    try {
      const s = await (await fetch("/api/status")).json();
      state.maxRows = s.max_rows; state.historyTurns = s.history_turns;
      els.dbName.textContent = s.db.database;
      const rows = [];
      rows.push(s.db.connected
        ? `<div class="status-row"><span class="dot dot-ok"></span><span class="ellipsis"><b>${esc(s.db.database)}</b> · read-only · ${s.db.tables} tables</span></div>`
        : `<div class="status-row"><span class="dot dot-bad"></span><span class="ellipsis">Database offline</span></div>`);
      rows.push(`<div class="status-row"><span class="dot dot-ok"></span><span class="ellipsis">${esc(prettyModel(s.llm.model))}</span></div>`);
      if (s.allowlist) rows.push(`<div class="status-row">${icon(I.shield, 13)}<span class="ellipsis">Allowlist: ${s.allowlist.length} tables</span></div>`);
      if (s.allowlist_missing?.length) rows.push(`<div class="status-row status-warn">${icon(I.alert, 13)}<span>Not found: ${esc(s.allowlist_missing.join(", "))}</span></div>`);
      if (s.two_step) rows.push(`<div class="status-row">${icon(I.info, 13)}<span>Large schema: tables picked per question</span></div>`);
      els.status.innerHTML = rows.join("");
      renderExamples(s.examples || []);
    } catch (e) {
      els.status.innerHTML = `<div class="status-row"><span class="dot dot-bad"></span><span>Server unreachable</span></div>`;
    }
  }

  function prettyModel(m) {
    return String(m).replace(/^gemini-/, "Gemini ").replace(/^gpt-/, "GPT-").replace(/^claude-/, "Claude ")
      .replace(/-flash-lite/, " Flash Lite").replace(/-flash/, " Flash").replace(/-pro/, " Pro");
  }

  function renderExamples(examples) {
    els.sideExamples.innerHTML = "";
    els.grid.innerHTML = "";
    examples.forEach((q, i) => {
      const link = document.createElement("button");
      link.className = "example-link"; link.textContent = q;
      link.onclick = () => { newConversation(); ask(q); };
      els.sideExamples.appendChild(link);

      const card = document.createElement("button");
      card.className = "example-card";
      card.innerHTML = `<span class="example-icon">${icon(EXAMPLE_ICONS[i % EXAMPLE_ICONS.length], 16)}</span><span>${esc(q)}</span>`;
      card.onclick = () => ask(q);
      els.grid.appendChild(card);
    });
  }

  async function loadSchema(refresh = false) {
    try {
      if (refresh) await fetch("/api/schema/refresh", { method: "POST" });
      const tables = await (await fetch("/api/schema")).json();
      els.schema.innerHTML = tables.map((t) => {
        const fks = new Set((t.foreign_keys || []).map((f) => f.column));
        const cols = t.columns.map((c) => {
          const badge = c.key === "PRI" ? '<span class="key-badge key-pk">PK</span>'
            : fks.has(c.name) ? '<span class="key-badge key-fk">FK</span>' : "";
          return `<li title="${esc(c.comment)}"><span class="cname">${esc(c.name)}</span>${badge}<span class="ctype">${esc(c.type)}</span></li>`;
        }).join("");
        return `<details class="schema-table">
          <summary><span class="chev">${icon(I.chevron, 13)}</span><span class="tname">${esc(t.name)}</span><span class="tcount">${t.rows.toLocaleString()}</span></summary>
          ${t.comment ? `<div class="schema-desc">${esc(t.comment)}</div>` : ""}
          <ul class="schema-cols">${cols}</ul></details>`;
      }).join("");
      if (refresh) toast("Schema reloaded");
    } catch (e) {
      els.schema.innerHTML = '<div class="example-link">Schema unavailable</div>';
    }
  }
  els.refresh.onclick = () => loadSchema(true);

  // ---------- conversation ----------
  function newConversation() {
    state.conversationId = null; state.turns = 0;
    if (state.thread) state.thread.remove();
    state.thread = null;
    els.empty.hidden = false;
    els.title.textContent = "New conversation";
    els.memory.hidden = true;
    els.input.placeholder = "Ask about aircraft, components, inspections or maintenance logs…";
    els.app.classList.remove("nav-open");
    els.input.focus();
  }
  els.newChat.onclick = newConversation;

  function thread() {
    if (!state.thread) {
      state.thread = document.createElement("div");
      state.thread.className = "thread";
      els.messages.appendChild(state.thread);
    }
    els.empty.hidden = true;
    return state.thread;
  }

  function scrollDown() { els.messages.scrollTop = els.messages.scrollHeight; }

  function addUser(text) {
    const el = document.createElement("div");
    el.className = "msg-user fade-in";
    el.innerHTML = `<div class="bubble">${esc(text)}</div>`;
    thread().appendChild(el);
  }

  function addBot(cardEl) {
    const el = document.createElement("div");
    el.className = "msg-bot fade-in";
    el.innerHTML = `<div class="avatar">${icon(I.plane, 15)}</div>`;
    el.appendChild(cardEl);
    thread().appendChild(el);
    return el;
  }

  const STEPS = ["Reading the schema", "Writing SQL", "Checking it's read-only", "Running on MySQL"];
  function loadingCard() {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<ul class="steps">${STEPS.map((s) => `<li><span class="step-icon"><span class="pending-dot"></span></span>${s}</li>`).join("")}</ul>`;
    const items = [...card.querySelectorAll("li")];
    let i = 0;
    const paint = () => items.forEach((li, j) => {
      li.className = j < i ? "done" : j === i ? "active" : "";
      li.querySelector(".step-icon").innerHTML = j < i ? `<span class="check">${icon(I.check, 15, 2.6)}</span>`
        : j === i ? '<span class="spinner"></span>' : '<span class="pending-dot"></span>';
    });
    paint();
    // The server does all steps in one request; advance the display on a timer and hold on the last step.
    const timer = setInterval(() => { if (i < items.length - 1) { i++; paint(); } }, 1400);
    card.stop = () => clearInterval(timer);
    return card;
  }

  async function ask(question) {
    question = question.trim();
    if (!question || state.busy) return;
    state.busy = true; updateSend();
    els.input.value = ""; autosize();
    els.app.classList.remove("nav-open");

    addUser(question);
    const loading = loadingCard();
    const wrap = addBot(loading);
    scrollDown();

    let card;
    try {
      const res = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, conversation_id: state.conversationId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`);
      state.conversationId = data.conversation_id;
      card = renderAnswer(data.answer);
      if (state.turns === 0) els.title.textContent = question;
      state.turns++;
      els.memory.hidden = false;
      const n = Math.min(state.turns, state.historyTurns);
      els.memory.textContent = `Follow-ups on · ${n} question${n === 1 ? "" : "s"} in memory`;
      els.input.placeholder = "Ask a follow-up, like \"only BLR\" or \"sort by cost\"…";
    } catch (e) {
      card = statusCard("danger", I.x, "Something went wrong", esc(e.message));
    }
    loading.stop();
    wrap.replaceChild(card, loading);
    state.busy = false; updateSend();
    wrap.scrollIntoView({ block: "start", behavior: "smooth" });
    els.input.focus();
  }

  // ---------- answer rendering ----------
  function statusCard(kind, ic, title, bodyHtml) {
    const card = document.createElement("div");
    card.className = `card card-${kind}`;
    card.innerHTML = `<div class="card-head">${icon(ic, 17)}${esc(title)}</div>${bodyHtml ? `<div class="answer-text">${bodyHtml}</div>` : ""}`;
    return card;
  }

  function renderAnswer(a) {
    let card;
    if (a.status === "ok") card = okCard(a);
    else if (a.status === "needs_clarification") card = clarifyCard(a);
    else if (a.status === "not_answerable") card = statusCard("muted", I.info, "Can't answer that from this database", esc(a.explanation));
    else if (a.status === "blocked") card = statusCard("danger", I.shield, "Blocked by safety checks", esc(a.message));
    else card = statusCard("danger", I.x, "Couldn't get an answer", esc(a.message));
    if (a.sql && a.status !== "ok") card.appendChild(sqlSection(a.sql, false));
    return card;
  }

  function okCard(a) {
    const card = document.createElement("div");
    card.className = "card";

    const p = document.createElement("p");
    p.className = "answer-text"; p.textContent = a.explanation || "Here are the results.";
    card.appendChild(p);

    const chips = [];
    chips.push(`<span class="chip">${icon(I.rows, 12)}<b>${a.rows.length.toLocaleString()}${a.truncated ? "+" : ""}</b> row${a.rows.length === 1 ? "" : "s"}</span>`);
    chips.push(`<span class="chip">${icon(I.clock, 12)}<b>${a.total_seconds.toFixed(1)}s</b></span>`);
    if (a.is_follow_up) chips.push(`<span class="chip chip-accent">${icon(I.link, 12)}Follow-up</span>`);
    if (a.attempts > 1) chips.push(`<span class="chip chip-ok">${icon(I.redo, 12)}Self-corrected</span>`);
    if (a.two_step) chips.push(`<span class="chip" title="Tables picked for this question">${icon(I.info, 12)}${esc(a.tables_used.join(", "))}</span>`);
    card.insertAdjacentHTML("beforeend", `<div class="chips">${chips.join("")}</div>`);

    if (a.truncated) {
      card.insertAdjacentHTML("beforeend", `<div class="notice">${icon(I.alert, 15)}<span>Showing the first ${state.maxRows.toLocaleString()} rows only; the full result has more. Narrow the question (a base, an aircraft, a date range) to see everything.</span></div>`);
    }

    card.appendChild(resultsTable(a.columns, a.rows));

    const actions = document.createElement("div");
    actions.className = "actions";
    const sql = sqlSection(a.sql, true);
    const toggle = button(`${icon(I.code, 14)}View SQL`, () => {
      sql.hidden = !sql.hidden;
      toggle.innerHTML = `${icon(I.code, 14)}${sql.hidden ? "View SQL" : "Hide SQL"}`;
    });
    actions.append(
      toggle,
      button(`${icon(I.copy, 14)}Copy SQL`, () => copy(a.sql, "SQL copied")),
    );
    if (a.rows.length) actions.append(button(`${icon(I.download, 14)}Download CSV`, () => downloadCsv(a.columns, a.rows)));
    card.append(actions, sql);
    return card;
  }

  function clarifyCard(a) {
    const card = document.createElement("div");
    card.className = "card card-warn";
    card.innerHTML = `<div class="card-head">${icon(I.help, 17)}Quick question</div><p class="answer-text">${esc(a.clarifying_question)}</p>`;
    const options = parseOptions(a.clarifying_question);
    if (options.length >= 2) {
      const row = document.createElement("div");
      row.className = "option-row";
      options.forEach((o) => {
        const b = document.createElement("button");
        b.className = "option-btn"; b.textContent = o;
        b.onclick = () => ask(o);
        row.appendChild(b);
      });
      card.appendChild(row);
    } else {
      card.insertAdjacentHTML("beforeend", '<div class="chips"><span class="chip">Reply below and I\'ll write the query</span></div>');
    }
    return card;
  }

  // "Options: 1) Most tasks, 2) Most inspections, or 3) Most hours." -> ["Most tasks", ...]
  function parseOptions(text) {
    const out = [];
    const re = /(?:^|\s|\()(\d)[).]\s*(.+?)(?=(?:,?\s*(?:or\s+)?\(?\d[).]\s)|[?.]?\s*$)/g;
    let m;
    while ((m = re.exec(text))) out.push(m[2].replace(/[,;]\s*(or)?\s*$/i, "").trim());
    return out.filter(Boolean).slice(0, 5);
  }

  function button(html, onClick) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn btn-sm"; b.innerHTML = html; b.onclick = onClick;
    return b;
  }

  function sqlSection(sql, hidden) {
    const wrap = document.createElement("div");
    wrap.className = "sql-panel"; wrap.hidden = hidden;
    wrap.innerHTML = `<pre>${highlightSql(formatSql(sql))}</pre>`;
    return wrap;
  }

  // ---------- results table ----------
  const RAW_NUMBER_COLS = /(^|_)(id|year|number|code)$|year_of/i;
  function resultsTable(columns, rows) {
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "table-wrap no-rows"; empty.textContent = "No matching records.";
      return empty;
    }
    const numeric = columns.map((_, j) => rows.every((r) => r[j] === null || typeof r[j] === "number"));
    let sortCol = -1, sortDir = 1, view = rows;

    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    const table = document.createElement("table");
    table.className = "results";
    wrap.appendChild(table);

    const fmt = (v, j) => {
      if (v === null) return '<span class="null">—</span>';
      if (numeric[j] && !RAW_NUMBER_COLS.test(columns[j])) {
        return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      }
      return esc(v);
    };
    const label = (c) => esc(c.replace(/_/g, " ").replace(/^\w/, (x) => x.toUpperCase()));

    function paint() {
      table.innerHTML = `<thead><tr>${columns.map((c, j) =>
        `<th class="${numeric[j] ? "num" : ""}" data-j="${j}">${label(c)}${sortCol === j ? `<span class="sort">${sortDir > 0 ? "▲" : "▼"}</span>` : ""}</th>`).join("")}</tr></thead>
        <tbody>${view.map((r) => `<tr>${r.map((v, j) => `<td class="${numeric[j] ? "num" : ""}">${fmt(v, j)}</td>`).join("")}</tr>`).join("")}</tbody>`;
      table.querySelectorAll("th").forEach((th) => (th.onclick = () => sortBy(+th.dataset.j)));
    }
    function sortBy(j) {
      sortDir = sortCol === j ? -sortDir : 1; sortCol = j;
      view = [...rows].sort((x, y) => {
        const a = x[j], b = y[j];
        if (a === null) return 1; if (b === null) return -1;
        return (numeric[j] ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true })) * sortDir;
      });
      paint();
    }
    paint();
    return wrap;
  }

  // ---------- SQL formatting + highlighting ----------
  function formatSql(sql) {
    // Line breaks before major clauses, outside string literals.
    return sql.split(/('(?:[^'\\]|\\.)*')/).map((part, i) => i % 2 ? part : part
      .replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|UNION(?: ALL)?)\s+/gi, "\n$1 ")
      .replace(/\s+((?:LEFT |RIGHT |INNER |CROSS )?(?:OUTER )?JOIN)\s+/gi, "\n$1 ")
      .replace(/\s+(AND|OR)\s+/g, "\n  $1 ")).join("");
  }
  const KW = "SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|ON|AND|OR|NOT|IN|IS|NULL|AS|GROUP|BY|ORDER|HAVING|LIMIT|DESC|ASC|DISTINCT|CASE|WHEN|THEN|ELSE|END|WITH|UNION|ALL|BETWEEN|LIKE|INTERVAL|CURRENT_DATE|TRUE|FALSE|DAY|MONTH|YEAR";
  const FN = "COUNT|SUM|AVG|MIN|MAX|ROUND|COALESCE|DATE_SUB|DATE_ADD|DATEDIFF|CURDATE|NOW|IFNULL|CONCAT|LOWER|UPPER";
  const TOKEN = new RegExp(`('(?:[^'\\\\]|\\\\.)*')|\\b(\\d+(?:\\.\\d+)?)\\b|\\b(${FN})(?=\\s*\\()|\\b(${KW})\\b`, "gi");
  function highlightSql(sql) {
    let out = "", last = 0, m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(sql))) {
      out += esc(sql.slice(last, m.index));
      const cls = m[1] ? "sql-str" : m[2] ? "sql-num" : m[3] ? "sql-fn" : "sql-kw";
      out += `<span class="${cls}">${esc(m[0])}</span>`;
      last = TOKEN.lastIndex;
    }
    return out + esc(sql.slice(last));
  }

  // ---------- utilities ----------
  function downloadCsv(columns, rows) {
    const cell = (v) => (v === null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
    const csv = [columns, ...rows].map((r) => r.map(cell).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "results.csv" });
    a.click(); URL.revokeObjectURL(url);
  }

  async function copy(text, msg) {
    try { await navigator.clipboard.writeText(text); toast(msg); } catch (e) { toast("Couldn't copy"); }
  }

  function toast(msg) {
    const t = Object.assign(document.createElement("div"), { className: "toast", textContent: msg });
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 1800);
  }

  // ---------- composer ----------
  function autosize() { els.input.style.height = "auto"; els.input.style.height = `${els.input.scrollHeight}px`; }
  function updateSend() { els.send.disabled = state.busy || !els.input.value.trim(); }
  els.input.addEventListener("input", () => { autosize(); updateSend(); });
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(els.input.value); }
  });
  els.composer.addEventListener("submit", (e) => { e.preventDefault(); ask(els.input.value); });

  loadStatus();
  loadSchema();
  els.input.focus();
})();
