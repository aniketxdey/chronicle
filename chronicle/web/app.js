const VIEWS = ["home", "record", "contexts", "workflows", "integrations", "settings"];
const TITLES = {
  home: "Home",
  record: "Record",
  contexts: "Contexts",
  workflows: "Workflows",
  integrations: "Integrations",
  settings: "Settings",
};
const ICONS = {
  home: '<path d="M3 10.5 12 4l9 6.5V20H3z"/><path d="M9 20v-6h6v6"/>',
  record: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/>',
  contexts: '<circle cx="6" cy="7" r="2.5"/><circle cx="18" cy="7" r="2.5"/><circle cx="12" cy="17" r="2.5"/><path d="M8 8.2 10.8 15M16 8.2 13.2 15M8.5 7h7"/>',
  workflows: '<rect x="3" y="4" width="6" height="5"/><rect x="15" y="4" width="6" height="5"/><rect x="9" y="15" width="6" height="5"/><path d="M6 9v3h12V9M12 12v3"/>',
  integrations: '<path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
};
const CONTEXT_TABS = [
  ["files", "Context files"],
  ["meetings", "Meetings"],
  ["graph", "Knowledge graph"],
  ["timeline", "Timeline"],
  ["search", "Search"],
];
const SHADES = ["#fafafa", "#cfcfcf", "#a6a6a6", "#808080", "#5e5e5e", "#454545", "#333333"];
const KIND_STYLE = {
  app: { fill: "#fafafa" },
  person: { fill: "#fafafa", ring: true },
  site: { fill: "#c8c8c8" },
  page: { fill: "#9e9e9e" },
  project: { fill: "#c8c8c8", ring: true },
  customer: { fill: "#e6e6e6", ring: true },
  ticket: { fill: "#7a7a7a" },
  pr: { fill: "#7a7a7a", ring: true },
  file: { fill: "#8f8f8f" },
  identifier: { fill: "#5c5c5c" },
  query: { fill: "#000000", stroke: "#fafafa" },
  topic: { fill: "#000000", stroke: "#a6a6a6" },
  meeting: { fill: "#000000", stroke: "#fafafa", ring: true },
  window: { fill: "#3a3a3a" },
  session: { fill: "#262626", stroke: "#fafafa" },
};
const PRESETS_DURATION = ["5m", "15m", "30m", "1h", "2h", "4h"];
const PROMPTS = ["What was I working on today?", "Summarize my meetings", "What should I automate?"];
const DESTINATIONS = [
  { name: "ChatGPT Projects and custom GPTs", how: "Add the file under Project files or GPT Knowledge.", format: "md" },
  { name: "Claude Projects", how: "Add the file to the project knowledge.", format: "md" },
  { name: "Gemini Gems and NotebookLM", how: "Upload the file as a source.", format: "md" },
  { name: "Cursor, Codex, and Claude Code", how: "Save into the repo as AGENTS.md or .cursor/rules/context.md.", format: "md" },
  { name: "Obsidian and Notion", how: "Drop the markdown into a vault or import it as a page.", format: "md" },
  { name: "OpenAI and Anthropic file search", how: "Upload to a vector store or the Files API for retrieval.", format: "json" },
  { name: "LangChain, LlamaIndex, custom RAG", how: "Load nodes, edges, and the context pack from one JSON file.", format: "json" },
  { name: "Neo4j", how: "Paste into Neo4j Browser or run with cypher-shell.", format: "cypher" },
  { name: "Gephi, yEd, Cytoscape", how: "Open the GraphML file to lay out and explore the graph.", format: "graphml" },
];
const PACKAGES = [
  { name: "Claude plugin", body: "Claude Code, Claude Desktop, and Cowork", cmd: "claude plugin marketplace add aniketxdey/chronicle\nclaude plugin install chronicle@chronicle" },
  { name: "Gemini CLI extension", body: "Installs the server and its instructions", cmd: "gemini extensions install https://github.com/aniketxdey/chronicle" },
  { name: "MCP Registry", body: "Any client that reads the official registry", cmd: "io.github.aniketxdey/chronicle" },
  { name: "Any MCP client", body: "Runs from PyPI without installing", cmd: "uvx chronicle-context mcp" },
];

const state = {
  ready: false,
  view: "home",
  step: 0,
  settings: null,
  health: null,
  overview: null,
  sessions: null,
  capture: null,
  saved: null,
  presets: null,
  ctx: null,
  newContext: { source: "", task: "", preset: "general" },
  workflowTab: "detected",
  workflows: null,
  pipes: null,
  clients: null,
  client: null,
  exportSource: "",
  exportPreset: "general",
  ai: null,
  speakers: null,
  bootError: "",
  poll: null,
};

const $ = (selector) => document.querySelector(selector);

const TOKEN_KEY = "chronicle-token";
(function readToken() {
  const params = new URLSearchParams(location.search);
  const token = params.get("token");
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
    params.delete("token");
    const query = params.toString();
    history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
  }
})();

function token() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

function media(url) {
  if (!url) return "";
  const value = token();
  return value ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(value)}` : url;
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function icon(name) {
  return `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

function clock(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function stamp(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function dayShort(isoDate) {
  const date = new Date(`${isoDate}T12:00:00`);
  return date.toLocaleDateString([], { weekday: "short" });
}

function mins(seconds) {
  const total = Math.max(0, Math.round((seconds || 0) / 60));
  if (total < 60) return `${total}m`;
  return `${Math.floor(total / 60)}h ${total % 60}m`;
}

function countdown(seconds) {
  const value = Math.max(0, Math.round(seconds || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = value % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

function bytes(size) {
  const n = Number(size) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function snippet(text, limit = 280) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= limit) return clean;
  return `${clean.slice(0, limit).trim()}…`;
}

function shortUrl(url) {
  try {
    const parts = new URL(url);
    return `${parts.host}${parts.pathname === "/" ? "" : parts.pathname}`;
  } catch {
    return url;
  }
}

function toast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
  } catch {
    toast("Copy failed; select the text instead");
  }
}

function download(name, text, type = "text/markdown") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (opts.body) opts.headers["Content-Type"] = "application/json";
  if (token()) opts.headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(path, opts);
  const text = await response.text();
  if (opts.raw) {
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return text;
  }
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { error: text };
    }
  }
  if (!response.ok) {
    if (response.status === 401) throw new Error("This server needs a token. Open it with ?token=YOUR_TOKEN once.");
    throw new Error(body?.error || `Request failed (${response.status})`);
  }
  return body;
}

function qs(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// App colours are grey levels by rank, so the same app keeps its shade on every page.
function appShade(app) {
  const rank = (state.overview?.apps || []).findIndex((item) => item.app_name === app);
  return SHADES[rank < 0 ? SHADES.length - 1 : Math.min(rank, SHADES.length - 1)];
}

function closeDrawer() {
  const drawer = $("#drawer");
  if (drawer) drawer.innerHTML = "";
}

/* Routing */

function hashFor() {
  if (state.view !== "contexts" || !state.ctx) return `#${state.view}`;
  const { kind, id, tab } = state.ctx;
  return `#contexts/${kind}${id ? `/${id}` : ""}/${tab}`;
}

function readHash() {
  const parts = location.hash.replace("#", "").split("/").filter(Boolean);
  const view = VIEWS.includes(parts[0]) ? parts[0] : "home";
  state.view = view;
  state.ctx = null;
  if (view === "contexts" && parts[1]) {
    const kind = parts[1];
    if (kind === "all") state.ctx = newCtx("all", null, parts[2]);
    else if ((kind === "session" || kind === "saved") && parts[2]) state.ctx = newCtx(kind, Number(parts[2]), parts[3]);
  }
}

function newCtx(kind, id, tab) {
  const valid = CONTEXT_TABS.some(([key]) => key === tab);
  return { kind, id, tab: valid ? tab : "files", eventType: "", graphKind: "", preview: null };
}

/* Loading */

async function loadSessions(limit = 100) {
  state.sessions = await api(`/sessions?limit=${limit}`);
}

function ctxScope() {
  const ctx = state.ctx;
  if (!ctx) return {};
  if (ctx.kind === "session") return { session_id: ctx.id };
  if (ctx.kind === "saved" && ctx.record) {
    if (ctx.record.session_id) return { session_id: ctx.record.session_id };
    return { start: ctx.record.start_at, end: ctx.record.end_at };
  }
  return {};
}

function ctxGraphSession() {
  return ctxScope().session_id || "";
}

function ctxExport(extra = {}) {
  const ctx = state.ctx;
  const base = ctx.kind === "saved" ? { context_id: ctx.id } : ctx.kind === "session" ? { session_id: ctx.id } : {};
  return media(`/contexts/export${qs({ ...base, ...extra })}`);
}

async function loadContext() {
  const ctx = state.ctx;
  if (ctx.kind === "saved") ctx.record = await api(`/contexts/${ctx.id}`);
  if (ctx.kind === "session") ctx.record = await api(`/sessions/${ctx.id}`);
  ctx.detail = await api(`/contexts/detail${qs(ctxScope())}`);
  if (!state.presets) state.presets = (await api("/contexts/presets")).data;
  if (ctx.tab === "graph") await loadCtxGraph();
  if (ctx.tab === "meetings" && !state.speakers) state.speakers = await api("/speakers/unnamed");
}

async function loadCtxGraph() {
  const ctx = state.ctx;
  const session = ctxGraphSession();
  ctx.graph = await api(`/graph${qs({ limit: 160, session_id: session })}`);
  ctx.graphSummary = await api(`/graph/summary${qs({ session_id: session })}`);
}

async function loadView(view) {
  if (view === "home") {
    state.overview = await api("/overview");
    await loadSessions(20);
  }
  if (view === "record") {
    await loadSessions(50);
    state.capture = await api("/capture/status");
  }
  if (view === "contexts") {
    if (!state.overview) state.overview = await api("/overview");
    if (state.ctx) {
      await loadContext();
    } else {
      await loadSessions(100);
      const listing = await api("/contexts");
      state.saved = listing.data;
      state.presets = listing.presets;
    }
  }
  if (view === "workflows") {
    state.workflows = await api("/workflows");
    state.pipes = await api("/pipes");
  }
  if (view === "integrations") {
    if (!state.clients) state.clients = await api("/clients");
    if (!state.presets) state.presets = (await api("/contexts/presets")).data;
    await loadSessions(50);
  }
  if (view === "settings") {
    state.ai = await api("/ai/status");
    state.settings = await api("/settings");
  }
  state.health = await api("/health");
}

function startPolling() {
  stopPolling();
  state.poll = setInterval(async () => {
    try {
      const before = state.health?.recording?.id || null;
      state.health = await api("/health");
      const after = state.health.recording?.id || null;
      if (state.view === "record") {
        if (before !== after) {
          await loadSessions(50);
          state.capture = await api("/capture/status");
          paintView();
        } else {
          updateLive();
        }
      }
      syncChrome();
    } catch {
      /* the next tick retries */
    }
  }, 2000);
}

function stopPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

async function go(view, extra = {}) {
  if (!VIEWS.includes(view)) return;
  closeDrawer();
  Object.assign(state, extra);
  state.view = view;
  if (view !== "contexts") state.ctx = null;
  history.replaceState(null, "", hashFor());
  await loadView(view);
  paint();
  $(".main")?.scrollTo(0, 0);
}

async function openContext(kind, id, tab = "files") {
  await go("contexts", { ctx: newCtx(kind, id, tab) });
}

/* Shell */

function bootHtml() {
  return `<div class="boot"><p>${esc(state.bootError || "Opening Chronicle…")}</p></div>`;
}

function onboardingHtml() {
  const steps = [
    {
      kicker: "Open source · runs on this machine",
      title: "Record your computer. Give agents the context.",
      body: "Chronicle records the frames on your screen and the system events around them (app switches, window focus, URLs, clipboard, idle) for as long as you choose. It turns that recording into a knowledge graph of apps, pages, files, tickets, people, and projects, then serves it to any computer-use agent over MCP.",
    },
    {
      kicker: "What is kept",
      title: "Everything stays in one folder on your disk.",
      body: "Screenshots, window titles, URLs, and clipboard text go into a SQLite file under ~/.chronicle. Keystrokes are never recorded. Password managers, System Settings, and private browsing windows are skipped. Emails and key-shaped secrets are rewritten before they are saved. Nothing is uploaded unless you point a remote agent at the server.",
    },
  ];
  const marks = [0, 1, 2].map((index) => `<i class="${index <= state.step ? "is-on" : ""}"></i>`).join("");
  if (state.step < 2) {
    const step = steps[state.step];
    return `<section class="onboard"><div class="panel">
      <div class="steps">${marks}</div>
      <p class="label">${esc(step.kicker)}</p>
      <h1>${esc(step.title)}</h1>
      <p class="lead">${esc(step.body)}</p>
      <div class="actions">
        ${state.step ? '<button class="btn" type="button" data-act="back">Back</button>' : ""}
        <button class="btn primary" type="button" data-act="next">Continue</button>
      </div>
    </div></section>`;
  }
  return `<section class="onboard"><div class="panel">
    <div class="steps">${marks}</div>
    <p class="label">Start</p>
    <h1>How do you want to begin?</h1>
    <p class="lead">A recording runs for a preset time, then its graph is built. On macOS, the app running Chronicle needs Screen Recording and Accessibility permission. The sample workday lets you try the graph and MCP tools without recording anything.</p>
    <div class="tiles">
      <article class="tile"><h3>Record 15 minutes</h3><p class="muted">Do your normal work. The graph is ready when the timer ends.</p><button class="btn primary" type="button" data-act="finish" data-start="record">Start recording</button></article>
      <article class="tile"><h3>Sample workday</h3><p class="muted">18 frames, 2 meetings, a ticket, a PR, and a customer deadline.</p><button class="btn" type="button" data-act="finish" data-start="demo">Load sample</button></article>
      <article class="tile"><h3>Start empty</h3><p class="muted">Connect an agent first and let it call start_recording.</p><button class="btn" type="button" data-act="finish" data-start="empty">Open Chronicle</button></article>
    </div>
    <div class="actions"><button class="btn" type="button" data-act="back">Back</button></div>
  </div></section>`;
}

function shellHtml() {
  const buttons = VIEWS.map(
    (view) => `<button class="nav-btn" type="button" data-act="nav" data-view="${view}" title="${TITLES[view]}">${icon(view)}<span>${TITLES[view]}</span></button>`,
  ).join("");
  return `<div class="shell">
    <aside class="side">
      <div class="brand"><svg class="logo" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 16 12 7l5 9z"/><circle cx="12" cy="7" r="2.2"/><circle cx="7" cy="16" r="2.2"/><circle cx="17" cy="16" r="2.2"/></svg><div><strong>Chronicle</strong><span>context for agents</span></div></div>
      <button class="rec" type="button" data-act="nav" data-view="record" title="Recording status"><i id="rec-dot" class="dot"></i><span id="rec-label">Not recording</span></button>
      <nav class="nav">${buttons}</nav>
      <div class="side-foot"><div id="graph-count"></div><div id="frame-count"></div><div><a href="/docs">API</a> · <a href="https://modelcontextprotocol.io" target="_blank" rel="noreferrer">MCP</a></div></div>
    </aside>
    <main class="main">
      <header class="head"><div id="crumbs" class="crumbs"></div><div id="head-actions" class="actions tight"></div></header>
      <div id="view" class="page"></div>
    </main>
  </div>
  <div id="drawer"></div>`;
}

function crumbs() {
  const parts = [`<button type="button" data-act="nav" data-view="${state.view}">${esc(TITLES[state.view])}</button>`];
  if (state.view === "contexts" && state.ctx) parts.push(`<span>${esc(ctxTitle())}</span>`);
  return parts.join('<i>/</i>');
}

function syncChrome() {
  document.querySelectorAll(".nav [data-act=nav]").forEach((button) => {
    button.classList.toggle("is-on", button.dataset.view === state.view);
  });
  const recording = state.health?.recording;
  $("#rec-dot")?.classList.toggle("is-on", Boolean(recording));
  const rec = $("#rec-label");
  if (rec) {
    rec.textContent = recording
      ? recording.remaining_secs != null
        ? `Recording · ${countdown(recording.remaining_secs)}`
        : `Recording · ${recording.elapsed}`
      : "Not recording";
  }
  const count = $("#frame-count");
  if (count && state.health) count.textContent = `${state.health.frames} frames · ${bytes(state.health.db_bytes)}`;
  const graph = $("#graph-count");
  if (graph && state.health) graph.textContent = `${state.health.graph_nodes} nodes · ${state.health.graph_edges} edges`;
  const trail = $("#crumbs");
  if (trail) trail.innerHTML = crumbs();
}

function emptyHtml() {
  return `<section class="panel-box hero">
    <p class="label">No history</p>
    <h2>Nothing recorded yet</h2>
    <p class="muted">Record a stretch of work, or load the sample workday to try contexts and the MCP tools first.</p>
    <div class="actions">
      <button class="btn primary" type="button" data-act="nav" data-view="record">Record</button>
      <button class="btn" type="button" data-act="demo">Load sample workday</button>
      <button class="btn" type="button" data-act="nav" data-view="integrations">Connect an agent</button>
    </div>
  </section>`;
}

function section(title, body, extra = "") {
  return `<section class="panel-box"><header class="box-head"><h2>${esc(title)}</h2>${extra}</header>${body}</section>`;
}

function stat(label, value) {
  return `<div class="stat"><span class="label">${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

/* Home */

function homeHtml() {
  if (!state.health?.frames) return `<div class="stack">${emptyHtml()}</div>`;
  const data = state.overview || { apps: [], daily: [], sessions: [], totals: {} };
  const week = data.daily.reduce((sum, day) => sum + day.seconds, 0);
  const stats = `<div class="stats">
    ${stat("Recorded, 7 days", mins(week))}
    ${stat("Sessions", state.health.sessions)}
    ${stat("Frames", state.health.frames.toLocaleString())}
    ${stat("Graph nodes", state.health.graph_nodes.toLocaleString())}
    ${stat("Saved contexts", data.totals.contexts || 0)}
  </div>`;

  const sessions = data.sessions
    .map((item) => {
      const total = Math.max(1, ...(item.apps || []).map((app) => app.seconds));
      const apps = (item.apps || [])
        .slice(0, 4)
        .map((app) => `<div class="bar-row"><span>${esc(app.app_name)}</span><div class="track"><span style="width:${Math.round((app.seconds / total) * 100)}%;background:${appShade(app.app_name)}"></span></div><span class="num">${mins(app.seconds)}</span></div>`)
        .join("");
      const tags = [...(item.topics || []), ...(item.queries || []).map((q) => `“${q}”`)]
        .slice(0, 6)
        .map((tag) => `<span class="tag">${esc(tag)}</span>`)
        .join("");
      const tasks = (item.tasks || []).map((task) => `<li>${esc(task)}</li>`).join("");
      return `<article class="session-card">
        <header><div><p class="label">${esc(item.status)} · ${esc(stamp(item.started_at))}</p><h3>${esc(item.label || `Recording ${item.id}`)}</h3></div><span class="num">${esc(item.elapsed)}</span></header>
        <p class="meta">${item.frames} frames · ${item.events} events</p>
        <div class="bars">${apps || '<p class="faint">No frames.</p>'}</div>
        ${tasks ? `<ul class="tasks">${tasks}</ul>` : ""}
        ${tags ? `<div class="tags">${tags}</div>` : ""}
        <div class="actions"><button class="btn primary" type="button" data-act="open-session" data-id="${item.id}">Open context</button><a class="btn" href="${media(`/contexts/export?session_id=${item.id}`)}" download>Download</a></div>
      </article>`;
    })
    .join("");

  const max = Math.max(1, ...data.daily.map((day) => day.seconds));
  const ranked = data.apps.slice(0, SHADES.length - 1).map((app) => app.app_name);
  const columns = data.daily
    .map((day) => {
      const parts = Object.entries(day.apps)
        .sort((a, b) => ranked.indexOf(a[0]) - ranked.indexOf(b[0]))
        .map(([app, seconds]) => `<span style="height:${((seconds / max) * 100).toFixed(1)}%;background:${appShade(app)}" title="${esc(app)} ${mins(seconds)}"></span>`)
        .join("");
      return `<div class="col"><div class="stackbar">${parts}</div><span class="num">${day.seconds ? mins(day.seconds) : "–"}</span><span class="label">${esc(dayShort(day.date))}</span></div>`;
    })
    .join("");
  const totalApps = Math.max(1, data.apps.reduce((sum, app) => sum + app.seconds, 0));
  const topApp = Math.max(1, data.apps[0]?.seconds || 0);
  const legend = data.apps
    .slice(0, 8)
    .map((app) => `<div class="bar-row"><span><i class="swatch" style="background:${appShade(app.app_name)}"></i>${esc(app.app_name)}</span><div class="track"><span style="width:${Math.round((app.seconds / topApp) * 100)}%;background:${appShade(app.app_name)}"></span></div><span class="num">${mins(app.seconds)} · ${Math.round((app.seconds / totalApps) * 100)}%</span></div>`)
    .join("");

  const rows = (state.sessions?.data || [])
    .map((item) => `<tr><td><button class="link" type="button" data-act="open-session" data-id="${item.id}">${esc(item.label || `Recording ${item.id}`)}</button></td><td>${esc(stamp(item.started_at))}</td><td class="num">${esc(item.elapsed)}</td><td class="num">${item.frames}</td><td><span class="pill ${item.status === "recording" ? "live" : ""}">${esc(item.status)}</span></td><td class="right"><button class="btn sm" type="button" data-act="open-session" data-id="${item.id}" data-tab="timeline">Timeline</button><button class="btn sm" type="button" data-act="open-session" data-id="${item.id}" data-tab="graph">Graph</button></td></tr>`)
    .join("");

  return `<div class="stack">
    ${stats}
    ${section("Last five sessions", `<div class="session-grid">${sessions || '<p class="muted pad">No recordings yet. Frames captured outside a session still show up under All history.</p>'}</div>`, '<button class="btn sm" type="button" data-act="nav" data-view="contexts">All contexts</button>')}
    <div class="grid-2">
      ${section("Time per day by app", `<div class="columns">${columns}</div>`, '<span class="faint">last 7 days</span>')}
      ${section("Top apps", `<div class="bars">${legend || '<p class="muted">No frames in the last 7 days.</p>'}</div>`)}
    </div>
    ${section("Recordings", `<table class="table"><thead><tr><th>Recording</th><th>Started</th><th class="num">Length</th><th class="num">Frames</th><th>Status</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="muted">No recordings yet.</td></tr>'}</tbody></table>`, '<button class="btn sm primary" type="button" data-act="nav" data-view="record">New recording</button>')}
  </div>`;
}

/* Record */

function recordHtml() {
  const active = state.health?.recording;
  const capture = state.capture || {};
  const caps = capture.capabilities || {};
  const capRow = [
    ["window", "Front window"],
    ["browser_url", "Browser URL"],
    ["screenshot", "Screenshots"],
    ["clipboard", "Clipboard"],
    ["idle", "Idle time"],
    ["ocr", "OCR (tesseract)"],
    ["accessibility", "Accessibility text"],
  ]
    .map(([key, label]) => `<span class="pill ${caps[key] ? "ok" : "off"}">${caps[key] ? "●" : "○"} ${esc(label)}</span>`)
    .join("");
  const live = active
    ? `<section class="panel-box live">
        <p class="label">Recording ${active.id}${active.label ? ` · ${esc(active.label)}` : ""}</p>
        <div class="big" id="live-clock">${active.remaining_secs != null ? countdown(active.remaining_secs) : esc(active.elapsed)}</div>
        <p class="meta" id="live-meta">${active.remaining_secs != null ? "left" : "elapsed, until stopped"} · ${active.frames} frames · ${active.events} events · every ${active.interval_secs}s</p>
        <div class="actions"><button class="btn danger" type="button" data-act="session-stop" data-id="${active.id}">Stop and build graph</button></div>
      </section>`
    : section(
        "New recording",
        `<p class="muted">Pick how long to record. Frames and system events are tied to this recording, and its graph is built when the time runs out.</p>
        <form class="stack" data-form="record">
          <div class="choices">${PRESETS_DURATION.map((value, index) => `<label class="choice"><input type="radio" name="duration" value="${value}" ${index === 2 ? "checked" : ""} />${value}</label>`).join("")}<label class="choice"><input type="radio" name="duration" value="open" />Until stopped</label></div>
          <div class="inline">
            <input name="label" placeholder="What are you about to do? (optional)" autocomplete="off" />
            <input name="interval" class="narrow" type="number" min="1" max="600" value="${esc(capture.interval_secs || 5)}" title="Seconds between samples" />
            <button class="btn primary" type="submit">Start recording</button>
          </div>
        </form>`,
      );
  const rows = (state.sessions?.data || [])
    .map(
      (item) => `<tr><td><strong>${esc(item.label || `Recording ${item.id}`)}</strong></td><td>${esc(stamp(item.started_at))}</td><td class="num">${esc(item.elapsed)}</td><td class="num">${item.frames}</td><td class="num">${item.events}</td><td><span class="pill ${item.status === "recording" ? "live" : ""}">${esc(item.status)}</span></td><td class="right">${
        item.status === "recording"
          ? `<button class="btn sm danger" type="button" data-act="session-stop" data-id="${item.id}">Stop</button>`
          : `<button class="btn sm" type="button" data-act="open-session" data-id="${item.id}">Open context</button><button class="btn sm" type="button" data-act="session-delete" data-id="${item.id}">Delete</button>`
      }</td></tr>`,
    )
    .join("");
  return `<div class="stack">
    ${live}
    ${capture.last_error ? `<p class="banner">${esc(capture.last_error)}</p>` : ""}
    ${section(
      "This machine",
      `<div class="pills">${capRow}</div>
      <p class="faint">Never recorded: keystrokes. Skipped apps: ${esc((capture.denied_apps || []).join(", "))}. ${capture.recorder ? `Recorder process: ${esc(capture.recorder)}.` : ""}</p>
      <p class="faint">CLI: <code>chronicle record 30m --label "..."</code>. Agents can call <code>start_recording</code> over MCP.</p>`,
    )}
    ${section("Recordings", `<table class="table"><thead><tr><th>Recording</th><th>Started</th><th class="num">Length</th><th class="num">Frames</th><th class="num">Events</th><th>Status</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="7" class="muted">No recordings yet.</td></tr>'}</tbody></table>`)}
  </div>`;
}

function updateLive() {
  const active = state.health?.recording;
  const clockNode = $("#live-clock");
  const meta = $("#live-meta");
  if (!active || !clockNode) return;
  clockNode.textContent = active.remaining_secs != null ? countdown(active.remaining_secs) : active.elapsed;
  if (meta) {
    meta.textContent = `${active.remaining_secs != null ? "left" : "elapsed, until stopped"} · ${active.frames} frames · ${active.events} events · every ${active.interval_secs}s`;
  }
}

/* Contexts: landing */

function sourceOptions(selected, { allLabel = "All history", minutes = true } = {}) {
  const options = [`<option value="">${esc(allLabel)}</option>`];
  if (minutes) {
    for (const [value, label] of [["m:60", "Last hour"], ["m:240", "Last 4 hours"], ["m:1440", "Last 24 hours"]]) {
      options.push(`<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`);
    }
  }
  for (const item of state.sessions?.data || []) {
    const value = `s:${item.id}`;
    options.push(`<option value="${value}" ${selected === value ? "selected" : ""}>Recording ${item.id}${item.label ? ` · ${esc(item.label)}` : ""} (${esc(item.elapsed)})</option>`);
  }
  return options.join("");
}

function presetOptions(selected) {
  return (state.presets || []).map((item) => `<option value="${esc(item.id)}" ${item.id === selected ? "selected" : ""}>${esc(item.title)}</option>`).join("");
}

function parseSource(value) {
  if (value?.startsWith("s:")) return { session_id: Number(value.slice(2)) };
  if (value?.startsWith("m:")) return { minutes: Number(value.slice(2)) };
  return {};
}

function presetTitle(id) {
  return (state.presets || []).find((item) => item.id === id)?.title || id || "General";
}

function contextsHtml() {
  if (state.ctx) return contextHtml();
  if (!state.health?.frames) return `<div class="stack">${emptyHtml()}</div>`;
  const draft = state.newContext;
  const sessions = (state.sessions?.data || [])
    .map((item) => `<tr class="click" data-act="open-session" data-id="${item.id}"><td><strong>${esc(item.label || `Recording ${item.id}`)}</strong></td><td>${esc(stamp(item.started_at))}</td><td class="num">${esc(item.elapsed)}</td><td class="num">${item.frames}</td><td class="num">${item.events}</td><td><span class="pill ${item.status === "recording" ? "live" : ""}">${esc(item.status)}</span></td><td class="right"><span class="faint">Open →</span></td></tr>`)
    .join("");
  const saved = (state.saved || [])
    .map((item) => `<tr class="click" data-act="open-saved" data-id="${item.id}"><td><strong>${esc(item.title)}</strong>${item.task && item.task !== item.title ? `<br><span class="faint">${esc(snippet(item.task, 90))}</span>` : ""}</td><td><span class="tag">${esc(presetTitle(item.preset))}</span></td><td>${item.session_id ? `Recording ${item.session_id}` : item.minutes ? `Last ${mins(item.minutes * 60)}` : "All history"}</td><td>${esc(stamp(item.created_at))}</td><td class="num">${Math.round(item.characters / 4).toLocaleString()}</td><td class="right"><a class="btn sm" href="${media(`/contexts/export?context_id=${item.id}`)}" download>Download</a><button class="btn sm" type="button" data-act="saved-delete" data-id="${item.id}">Delete</button></td></tr>`)
    .join("");
  const prompts = PROMPTS.map((prompt) => `<button class="tag click" type="button" data-act="prompt" data-q="${esc(prompt)}">${esc(prompt)}</button>`).join("");
  return `<div class="stack">
    <div class="grid-2 even">
      <section class="panel-box choice-box">
        <p class="label">Option 1</p>
        <h2>Open a past session</h2>
        <p class="muted">Every recording is a context: its downloadable files, meetings, knowledge graph, timeline, and search.</p>
        <div class="actions"><button class="btn" type="button" data-act="jump" data-target="past">Browse ${(state.sessions?.data || []).length} sessions</button><button class="btn" type="button" data-act="open-all">All history</button></div>
      </section>
      <section class="panel-box choice-box">
        <p class="label">Option 2</p>
        <h2>Generate a new context</h2>
        <form class="stack" data-form="new-context">
          <input name="task" value="${esc(draft.task)}" placeholder="Task for the agent, e.g. finish the Acme export (optional)" autocomplete="off" />
          <div class="inline">
            <label class="field">From<select name="source">${sourceOptions(draft.source)}</select></label>
            <label class="field">Workflow<select name="preset">${presetOptions(draft.preset)}</select></label>
          </div>
          <div class="actions"><button class="btn primary" type="submit">Generate context</button></div>
        </form>
      </section>
    </div>
    ${section("Generated contexts", `<table class="table"><thead><tr><th>Context</th><th>Workflow</th><th>From</th><th>Created</th><th class="num">Tokens</th><th></th></tr></thead><tbody>${saved || '<tr><td colspan="6" class="muted">Nothing generated yet. Generated contexts are saved here so you can reopen and download them.</td></tr>'}</tbody></table>`)}
    <div id="past">${section("Past sessions", `<table class="table"><thead><tr><th>Recording</th><th>Started</th><th class="num">Length</th><th class="num">Frames</th><th class="num">Events</th><th>Status</th><th></th></tr></thead><tbody><tr class="click" data-act="open-all"><td><strong>All history</strong><br><span class="faint">Every frame, with older sessions weighted less</span></td><td></td><td></td><td class="num">${state.health.frames}</td><td class="num">${state.health.events ?? ""}</td><td></td><td class="right"><span class="faint">Open →</span></td></tr>${sessions}</tbody></table>`)}</div>
    ${section("Ask across your history", `<form class="inline" data-form="ask"><input name="question" placeholder="Ask what you saw, heard, or still owe" autocomplete="off" /><button class="btn primary" type="submit">Ask</button></form><div class="tags">${prompts}</div><div id="answer" class="answer" hidden></div>`)}
  </div>`;
}

/* Contexts: one context */

function ctxTitle() {
  const ctx = state.ctx;
  if (!ctx) return "";
  if (ctx.kind === "all") return "All history";
  if (ctx.kind === "saved") return ctx.record?.title || `Context ${ctx.id}`;
  return ctx.record?.label || `Recording ${ctx.id}`;
}

function contextHtml() {
  const ctx = state.ctx;
  const detail = ctx.detail || { frames: [], events: [], meetings: [], urls: [], blocks: [], apps: [], scope: {} };
  const scope = detail.scope || {};
  const seconds = detail.apps.reduce((sum, app) => sum + app.seconds, 0);
  const source = ctx.kind === "saved"
    ? `${presetTitle(ctx.record?.preset)} · ${ctx.record?.session_id ? `Recording ${ctx.record.session_id}` : scope.label}`
    : ctx.kind === "session" ? `Recording ${ctx.id} · ${esc(ctx.record?.status || "")}` : "Every frame and session";
  const tabs = CONTEXT_TABS.map(([key, label]) => {
    const count = key === "meetings" ? detail.meetings.length : key === "timeline" ? detail.frames.length : null;
    return `<button class="tab ${ctx.tab === key ? "is-on" : ""}" type="button" data-act="ctx-tab" data-tab="${key}">${esc(label)}${count != null ? `<span class="count">${count}</span>` : ""}</button>`;
  }).join("");
  const bodies = { files: ctxFilesHtml, meetings: ctxMeetingsHtml, graph: ctxGraphHtml, timeline: ctxTimelineHtml, search: ctxSearchHtml };
  return `<div class="stack">
    <section class="ctx-head">
      <div>
        <p class="label">${source}</p>
        <h1>${esc(ctxTitle())}</h1>
        <p class="meta">${scope.start ? `${esc(stamp(scope.start))} – ${esc(stamp(scope.end))} · ` : ""}${mins(seconds)} on screen · ${detail.frames.length} frames · ${detail.events.length} system events · ${detail.meetings.length} meetings · ${detail.urls.length} URLs</p>
      </div>
      <div class="actions tight"><button class="btn" type="button" data-act="nav" data-view="contexts">All contexts</button><a class="btn primary" href="${ctxExport()}" download>Download context</a></div>
    </section>
    <nav class="tabs">${tabs}</nav>
    ${(bodies[ctx.tab] || ctxFilesHtml)()}
  </div>`;
}

function ctxFilesHtml() {
  const ctx = state.ctx;
  const savedPreset = ctx.kind === "saved" ? ctx.record?.preset : null;
  const cards = (state.presets || [])
    .map((item) => {
      const on = ctx.preview?.preset === item.id;
      return `<article class="file-card ${on ? "is-on" : ""}">
        <header><h3>${esc(item.title)}</h3>${item.id === savedPreset ? '<span class="pill">saved</span>' : ""}</header>
        <p class="muted">${esc(item.brief)}</p>
        <div class="actions"><a class="btn sm" href="${ctxExport({ preset: item.id })}" download>Download .md</a><button class="btn sm" type="button" data-act="ctx-preview" data-preset="${esc(item.id)}">${on ? "Hide" : "Preview"}</button></div>
      </article>`;
    })
    .join("");
  const preview = ctx.preview
    ? section(
        `Preview · ${presetTitle(ctx.preview.preset)}`,
        `<p class="meta">${ctx.preview.text.length.toLocaleString()} characters · about ${Math.round(ctx.preview.text.length / 4).toLocaleString()} tokens</p><pre class="doc">${esc(ctx.preview.text)}</pre>`,
        '<button class="btn sm" type="button" data-act="ctx-copy">Copy</button>',
      )
    : "";
  const graphFiles = [["json", "JSON (context + graph)"], ["graphml", "GraphML"], ["cypher", "Neo4j Cypher"]]
    .map(([format, label]) => `<a class="btn sm" href="${ctxExport({ format })}" download>${label}</a>`)
    .join("");
  return `<div class="stack">
    <p class="muted">Each file is the context an agent gets from <code>get_context</code>, focused on one kind of work from this ${ctx.kind === "session" ? "recording" : "context"}. Download one and hand it to any agent, project, or knowledge base.</p>
    <div class="file-grid">${cards}</div>
    ${preview}
    ${section("Graph exports", `<p class="muted">The full knowledge graph for this scope, for graph databases and RAG pipelines.</p><div class="actions">${graphFiles}</div>`)}
  </div>`;
}

function ctxMeetingsHtml() {
  const meetings = state.ctx.detail?.meetings || [];
  const unnamed = state.speakers?.data || [];
  const naming = unnamed
    .map((speaker) => `<form class="inline" data-form="speaker"><input type="hidden" name="id" value="${speaker.id}" /><input name="name" placeholder="Name this speaker (${speaker.segments} line${speaker.segments === 1 ? "" : "s"})" required /><button class="btn primary" type="submit">Save</button></form>`)
    .join("");
  const cards = meetings
    .map((meeting, index) => {
      const notes = meeting.notes || { decisions: [], action_items: [], participants: [] };
      const list = (items) => items.map((item) => `<li><strong>${esc(item.speaker)}</strong> ${esc(item.text)}</li>`).join("");
      const lines = (meeting.segments || [])
        .map((segment) => `<div class="line"><span class="who">${esc(segment.speaker_name || "Unnamed speaker")}</span><span class="faint">${esc(clock(segment.timestamp))}</span><div>${esc(segment.transcription)}</div></div>`)
        .join("");
      return `<article class="panel-box meeting">
        <header class="box-head"><div><h2>${esc(meeting.title || "Meeting")}</h2><p class="meta">${esc(stamp(meeting.started_at))} – ${esc(clock(meeting.ended_at))} · ${esc(meeting.app_name || "")}</p></div><button class="btn sm" type="button" data-act="meeting-notes" data-index="${index}">Download notes</button></header>
        <p>${esc(notes.summary)}</p>
        <div class="grid-2 even">
          <div><p class="label">Decisions</p>${notes.decisions.length ? `<ul class="notes">${list(notes.decisions)}</ul>` : '<p class="faint">None found in the transcript.</p>'}</div>
          <div><p class="label">Action items</p>${notes.action_items.length ? `<ul class="notes">${list(notes.action_items)}</ul>` : '<p class="faint">None found in the transcript.</p>'}</div>
        </div>
        <details><summary>Transcript · ${(meeting.segments || []).length} lines</summary>${lines}</details>
      </article>`;
    })
    .join("");
  return `<div class="stack">
    ${naming ? section("Unnamed speakers", `<p class="muted">Naming writes the label onto every line from that voice.</p><div class="stack">${naming}</div>`) : ""}
    ${cards || section("No meetings in this context", '<p class="muted">Transcripts come from any speech-to-text tool that posts to <code>POST /add</code> with <code>content_type=audio</code>. Meetings that overlap this context show up here with notes.</p>')}
  </div>`;
}

function layout(nodes, edges, width, height) {
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const points = nodes.map((node, i) => {
    const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
    const radius = 0.3 + (0.1 * ((i * 7919) % 10)) / 10;
    return { x: width / 2 + Math.cos(angle) * width * radius, y: height / 2 + Math.sin(angle) * height * radius, vx: 0, vy: 0 };
  });
  const links = edges
    .filter((edge) => index.has(edge.src) && index.has(edge.dst))
    .map((edge) => [index.get(edge.src), index.get(edge.dst), edge.weight]);
  const k = Math.sqrt((width * height) / Math.max(1, nodes.length)) * 0.6;
  const steps = 300;
  for (let step = 0; step < steps; step += 1) {
    const heat = (width / 12) * (1 - step / steps) + 0.5;
    for (const point of points) {
      point.vx = 0;
      point.vy = 0;
    }
    for (let a = 0; a < points.length; a += 1) {
      for (let b = a + 1; b < points.length; b += 1) {
        const dx = points[a].x - points[b].x;
        const dy = points[a].y - points[b].y;
        const dist = Math.max(0.5, Math.hypot(dx, dy));
        const push = (k * k) / dist;
        points[a].vx += (dx / dist) * push;
        points[a].vy += (dy / dist) * push;
        points[b].vx -= (dx / dist) * push;
        points[b].vy -= (dy / dist) * push;
      }
    }
    for (const [a, b, weight] of links) {
      const dx = points[a].x - points[b].x;
      const dy = points[a].y - points[b].y;
      const dist = Math.max(0.5, Math.hypot(dx, dy));
      const pull = ((dist * dist) / k) * Math.min(1.6, 0.35 + Math.log1p(weight) * 0.25);
      points[a].vx -= (dx / dist) * pull;
      points[a].vy -= (dy / dist) * pull;
      points[b].vx += (dx / dist) * pull;
      points[b].vy += (dy / dist) * pull;
    }
    for (const point of points) {
      point.vx += (width / 2 - point.x) * 0.9;
      point.vy += (height / 2 - point.y) * 1.4;
      const length = Math.max(0.01, Math.hypot(point.vx, point.vy));
      const move = Math.min(length, heat);
      point.x = Math.min(width - 24, Math.max(24, point.x + (point.vx / length) * move));
      point.y = Math.min(height - 18, Math.max(18, point.y + (point.vy / length) * move));
    }
  }
  return { points, links };
}

function kindSwatch(kind) {
  const style = KIND_STYLE[kind] || { fill: "#808080" };
  return `<i class="swatch" style="background:${style.fill};${style.stroke ? `box-shadow:inset 0 0 0 1.5px ${style.stroke}` : ""}"></i>`;
}

function graphSvg() {
  const ctx = state.ctx;
  const graph = ctx.graph || { nodes: [], edges: [] };
  const nodes = graph.nodes.filter((node) => (ctx.graphKind ? node.kind === ctx.graphKind || node.id === ctx.node?.node?.id : node.kind !== "window"));
  const ids = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => ids.has(edge.src) && ids.has(edge.dst) && edge.rel !== "has_window");
  if (!nodes.length) return '<p class="muted pad">No graph nodes in this scope yet. Rebuild the graph, or record more.</p>';
  const width = 960;
  const height = 600;
  const { points, links } = layout(nodes, edges, width, height);
  const maxWeight = Math.max(1, ...nodes.map((node) => node.seconds || node.weight || 1));
  const selected = ctx.node?.node?.id;
  const neighbors = new Set((ctx.node?.neighbors || []).map((item) => item.id));
  const lines = links
    .map(([a, b, weight]) => {
      const lit = selected && (nodes[a].id === selected || nodes[b].id === selected);
      return `<line x1="${points[a].x.toFixed(1)}" y1="${points[a].y.toFixed(1)}" x2="${points[b].x.toFixed(1)}" y2="${points[b].y.toFixed(1)}" class="${lit ? "lit" : ""}" stroke-width="${Math.min(3, 0.4 + Math.log1p(weight) * 0.5).toFixed(2)}" />`;
    })
    .join("");
  const labelled = new Set([...nodes].sort((x, y) => (y.seconds || y.weight) - (x.seconds || x.weight)).slice(0, 28).map((node) => node.id));
  const circles = nodes
    .map((node, i) => {
      const style = KIND_STYLE[node.kind] || { fill: "#808080" };
      const size = 4 + Math.sqrt((node.seconds || node.weight || 1) / maxWeight) * 14;
      const dim = selected && node.id !== selected && !neighbors.has(node.id) ? "dim" : "";
      const x = points[i].x.toFixed(1);
      const y = points[i].y.toFixed(1);
      const ring = style.ring ? `<circle class="ring" cx="${x}" cy="${y}" r="${(size + 3).toFixed(1)}" />` : "";
      const label = labelled.has(node.id) || node.id === selected || neighbors.has(node.id)
        ? `<text x="${(points[i].x + size + 4).toFixed(1)}" y="${(points[i].y + 4).toFixed(1)}">${esc(snippet(node.label, 28))}</text>`
        : "";
      return `<g class="gnode ${dim} ${node.id === selected ? "is-on" : ""}" data-act="node" data-id="${esc(node.id)}"><title>[${esc(node.kind)}] ${esc(node.label)}</title>${ring}<circle cx="${x}" cy="${y}" r="${size.toFixed(1)}" fill="${style.fill}" stroke="${style.stroke || "#000"}" />${label}</g>`;
    })
    .join("");
  return `<svg class="graph" viewBox="0 0 ${width} ${height}" role="img" aria-label="Knowledge graph"><g class="edges">${lines}</g>${circles}</svg>`;
}

function nodePanel() {
  const data = state.ctx.node;
  if (!data) {
    return section("Inspector", '<p class="muted">Click a node to see what it connects to and which frames it came from. Window nodes are hidden unless you filter by kind.</p>');
  }
  const node = data.node;
  const evidence = (node.evidence || [])
    .map((item) =>
      item.type === "frame"
        ? `<button class="tag click" type="button" data-act="frame" data-id="${item.id}">frame ${item.id} · ${esc(clock(item.timestamp))}</button>`
        : `<span class="tag">${esc(item.type)} ${item.id} · ${esc(clock(item.timestamp))}</span>`,
    )
    .join("");
  const rows = (data.neighbors || [])
    .map((item) => `<button class="list-row" type="button" data-act="node" data-id="${esc(item.id)}"><span class="kind">${kindSwatch(item.kind)}${esc(item.kind)}</span><span>${esc(item.direction === "out" ? "→" : "←")} <em>${esc(item.rel)}</em> <strong>${esc(item.label)}</strong></span><span class="num faint">${Math.round(item.weight)}</span></button>`)
    .join("");
  return section(
    node.label,
    `<p class="label">${kindSwatch(node.kind)}${esc(node.kind)}</p>
    <dl class="props"><dt>ID</dt><dd><code>${esc(node.id)}</code></dd><dt>Seen</dt><dd>${Math.round(node.weight)}×</dd><dt>On screen</dt><dd>${mins(node.seconds)}</dd><dt>First</dt><dd>${esc(stamp(node.first_seen))}</dd><dt>Last</dt><dd>${esc(stamp(node.last_seen))}</dd></dl>
    <div class="tags">${evidence}</div>
    <p class="label">Neighbors</p>
    <div class="list">${rows || '<p class="faint">No neighbors.</p>'}</div>
    <div class="actions"><button class="btn sm" type="button" data-act="node-context" data-label="${esc(node.label)}">Generate context for this</button></div>`,
  );
}

function ctxGraphHtml() {
  const ctx = state.ctx;
  const summary = ctx.graphSummary || { counts: {}, transitions: [] };
  const kinds = Object.entries(summary.counts || {})
    .filter(([kind]) => !["task", "topic_cluster"].includes(kind))
    .map(([kind, count]) => `<button class="tag click ${ctx.graphKind === kind ? "is-on" : ""}" type="button" data-act="graph-kind" data-kind="${esc(kind)}">${kindSwatch(kind)}${esc(kind)} <span class="num">${count}</span></button>`)
    .join("");
  const transitions = (summary.transitions || [])
    .slice(0, 8)
    .map((item) => `<div class="line flat"><span>${esc(item.from)} → ${esc(item.to)}</span><span class="num faint">${Math.round(item.count)}×</span></div>`)
    .join("");
  const results = ctx.graphResults
    ? `<div class="list">${ctx.graphResults.map((item) => `<button class="list-row" type="button" data-act="node" data-id="${esc(item.id)}"><span class="kind">${kindSwatch(item.kind)}${esc(item.kind)}</span><span><strong>${esc(item.label)}</strong></span><span class="num faint">${mins(item.seconds)}</span></button>`).join("") || '<p class="faint">No entity matches.</p>'}</div>`
    : "";
  return `<div class="stack">
    <form class="inline" data-form="graph">
      <input name="q" value="${esc(ctx.graphQuery || "")}" placeholder="Find an entity: BILL-142, export.ts, Maya…" autocomplete="off" />
      <button class="btn primary" type="submit">Find</button>
      <button class="btn" type="button" data-act="graph-build">Rebuild</button>
    </form>
    <div class="tags">${kinds}${ctx.graphKind ? '<button class="tag click" type="button" data-act="graph-kind" data-kind="">Show all</button>' : ""}</div>
    ${results}
    <div class="graph-grid">
      <section class="panel-box graph-box">${graphSvg()}<p class="faint pad">${esc(summary.scope || "all")} · built ${esc(stamp(summary.built_at) || "never")} · ${ctx.graph?.nodes?.length || 0} heaviest nodes shown</p></section>
      <div class="stack">${nodePanel()}${transitions ? section("Habitual transitions", transitions) : ""}</div>
    </div>
  </div>`;
}

function ctxTimelineHtml() {
  const ctx = state.ctx;
  const detail = ctx.detail || { blocks: [], frames: [], events: [], urls: [], tasks: [] };
  const blocks = detail.blocks || [];
  let axis = "";
  let marks = "";
  if (blocks.length) {
    const start = Math.min(...blocks.map((block) => new Date(block.start).getTime()));
    const end = Math.max(...blocks.map((block) => new Date(block.end).getTime()));
    const span = Math.max(60000, end - start);
    marks = blocks
      .map((block) => {
        const left = ((new Date(block.start).getTime() - start) / span) * 100;
        const width = Math.max(0.6, ((new Date(block.end).getTime() - new Date(block.start).getTime()) / span) * 100);
        return `<button class="block" type="button" data-act="frame" data-id="${block.frame_id}" title="${esc(block.app_name)} · ${esc(block.window_name)} · ${esc(clock(block.start))}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%;background:${appShade(block.app_name)}"></button>`;
      })
      .join("");
    const ticks = [];
    for (let i = 0; i <= 6; i += 1) {
      const at = new Date(start + (span * i) / 6);
      ticks.push(`<span>${esc(span > 36 * 3600000 ? at.toLocaleDateString([], { month: "short", day: "numeric" }) : clock(at.toISOString()))}</span>`);
    }
    axis = `<div class="ticks">${ticks.join("")}</div>`;
  }
  const appLegend = (detail.apps || []).slice(0, 7).map((app) => `<span class="tag">${`<i class="swatch" style="background:${appShade(app.app_name)}"></i>`}${esc(app.app_name)} <span class="num">${mins(app.seconds)}</span></span>`).join("");
  const frames = detail.frames.slice(-240);
  const film = frames
    .map((frame) => {
      const shot = frame.media_url
        ? `<img src="${esc(media(frame.media_url))}" alt="${esc(frame.app_name)} ${esc(frame.window_name)}" loading="lazy" />`
        : `<div class="ph">${esc(frame.app_name)}</div>`;
      return `<button class="thumb" type="button" data-act="frame" data-id="${frame.frame_id}">${shot}<span><b>${esc(clock(frame.timestamp))}</b> ${esc(frame.app_name)}</span></button>`;
    })
    .join("");
  const tasks = (detail.tasks || [])
    .map((task) => `<tr><td class="num">${esc(clock(task.start))}–${esc(clock(task.end))}</td><td>${esc(task.label)}</td><td class="num">${mins(task.duration_secs)}</td></tr>`)
    .join("");
  const types = [...new Set(detail.events.map((event) => event.event_type))];
  const typeTags = ["", ...types].map((type) => `<button class="tag click ${ctx.eventType === type ? "is-on" : ""}" type="button" data-act="event-type" data-type="${esc(type)}">${esc(type || "all")}</button>`).join("");
  const events = detail.events
    .filter((event) => !ctx.eventType || event.event_type === ctx.eventType)
    .slice(-300)
    .map((event) => `<tr ${event.frame_id ? `class="click" data-act="frame" data-id="${event.frame_id}"` : ""}><td class="num">${esc(clock(event.timestamp))}</td><td><span class="kind">${esc(event.event_type)}</span></td><td>${esc(event.app_name || "")}</td><td>${esc(snippet(event.text, 140))}</td></tr>`)
    .join("");
  const urls = (detail.urls || [])
    .slice(0, 40)
    .map((item) => `<tr class="click" data-act="frame" data-id="${item.first_frame}"><td>${esc(snippet(item.title, 60))}</td><td><a href="${esc(item.url)}" target="_blank" rel="noreferrer">${esc(snippet(shortUrl(item.url), 70))}</a></td><td class="num">${mins(item.seconds)}</td></tr>`)
    .join("");
  return `<div class="stack">
    ${section("Activity", blocks.length ? `<div class="track-lane">${marks}</div>${axis}<div class="tags">${appLegend}</div>` : '<p class="muted">No frames in this context.</p>')}
    ${section("Frames", `<div class="film">${film || '<p class="muted">No frames.</p>'}</div>`, `<span class="faint">${detail.frames.length > frames.length ? `latest ${frames.length} of ${detail.frames.length}` : `${frames.length} frames`}</span>`)}
    <div class="grid-2">
      ${section("System events", `<div class="tags">${typeTags}</div><div class="scroll"><table class="table"><tbody>${events || '<tr><td class="muted">No events.</td></tr>'}</tbody></table></div>`)}
      <div class="stack">
        ${section("Tasks", `<table class="table"><tbody>${tasks || '<tr><td class="muted">No tasks segmented yet.</td></tr>'}</tbody></table>`)}
        ${section("URLs", `<div class="scroll"><table class="table"><tbody>${urls || '<tr><td class="muted">No browser URLs.</td></tr>'}</tbody></table></div>`)}
      </div>
    </div>
  </div>`;
}

function ctxSearchHtml() {
  const ctx = state.ctx;
  const found = ctx.find;
  let results = '<p class="muted">Search this context: graph nodes, screen text, transcripts, system events, URLs, and saved context files.</p>';
  if (found) {
    const nodes = found.nodes.map((item) => `<button class="list-row" type="button" data-act="search-node" data-id="${esc(item.id)}"><span class="kind">${kindSwatch(item.kind)}${esc(item.kind)}</span><span><strong>${esc(item.label)}</strong></span><span class="num faint">${mins(item.seconds)}</span></button>`).join("");
    const content = found.content.map((item, index) => {
      const body = item.content || {};
      const title = body.app_name || body.speaker_name || item.type;
      const detail = body.window_name || body.event_type || "";
      return `<button class="list-row" type="button" data-act="result" data-index="${index}"><span class="kind">${esc(item.type)}</span><span><strong>${esc(title)}</strong> ${esc(detail)}<br><span class="snippet">${esc(snippet(body.text, 220))}</span></span><span class="num faint">${esc(clock(body.timestamp))}</span></button>`;
    }).join("");
    const urls = found.urls.map((item) => `<button class="list-row" type="button" data-act="frame" data-id="${item.first_frame}"><span class="kind">url</span><span><strong>${esc(snippet(item.title, 80))}</strong><br><span class="snippet">${esc(item.url)}</span></span><span class="num faint">${item.frames}</span></button>`).join("");
    const files = found.contexts.map((item) => `<button class="list-row" type="button" data-act="open-saved" data-id="${item.id}"><span class="kind">context</span><span><strong>${esc(item.title)}</strong><br><span class="snippet">${esc(item.snippet)}</span></span><span class="num faint">${esc(stamp(item.created_at))}</span></button>`).join("");
    const group = (title, body, count) => section(`${title} · ${count}`, body ? `<div class="list">${body}</div>` : '<p class="faint">No matches.</p>');
    results = `<div class="grid-2 even">
      <div class="stack">${group("Graph nodes", nodes, found.nodes.length)}${group("URLs", urls, found.urls.length)}${group("Context files", files, found.contexts.length)}</div>
      <div class="stack">${group("Screen text, transcripts, and events", content, found.content.length)}</div>
    </div>`;
  }
  return `<div class="stack">
    <form class="inline" data-form="ctx-search"><input name="q" value="${esc(ctx.findQ || "")}" placeholder="invoice_id, Maya, a URL, a ticket…" autocomplete="off" /><button class="btn primary" type="submit">Search</button></form>
    ${results}
  </div>`;
}

/* Workflows */

function workflowsHtml() {
  const tab = state.workflowTab;
  const tabs = [["detected", "Detected workflows", (state.workflows?.data || []).length], ["pipes", "Pipes", (state.pipes?.data || []).length]]
    .map(([key, label, count]) => `<button class="tab ${tab === key ? "is-on" : ""}" type="button" data-act="workflow-tab" data-tab="${key}">${label}<span class="count">${count}</span></button>`)
    .join("");
  let body;
  if (tab === "pipes") {
    const installed = (state.pipes?.data || [])
      .map((pipe) => {
        const on = Boolean(pipe.enabled);
        return `<article class="panel-box"><header class="box-head"><h2>${esc(pipe.name)}</h2><span class="pill ${on ? "ok" : "off"}">${on ? "scheduled" : "off"}</span></header><p class="muted">${esc(pipe.description || "")}</p><p class="meta">${esc(pipe.schedule || "manual")} · last run ${esc(pipe.last_run ? stamp(pipe.last_run) : "never")}</p><details><summary>Prompt</summary><pre>${esc(pipe.prompt || "")}</pre></details><div class="actions"><button class="btn sm" type="button" data-act="pipe-toggle" data-name="${esc(pipe.name)}" data-enabled="${on ? "1" : "0"}">${on ? "Pause" : "Schedule"}</button><button class="btn sm primary" type="button" data-act="pipe-run" data-name="${esc(pipe.name)}">Run now</button><button class="btn sm" type="button" data-act="pipe-logs" data-name="${esc(pipe.name)}">Logs</button></div></article>`;
      })
      .join("");
    const library = (state.pipes?.discover || [])
      .map((pipe) => `<tr><td><strong>${esc(pipe.name)}</strong><br><span class="faint">${esc(pipe.description)}</span></td><td>${esc(pipe.schedule)}</td><td class="right"><button class="btn sm" type="button" data-act="pipe-install" data-name="${esc(pipe.name)}">Install</button></td></tr>`)
      .join("");
    body = `<p class="muted">A pipe is a prompt plus a schedule. Running one writes a markdown report from local history. Pipes live as files under ~/.chronicle/pipes.</p>
      <div class="card-grid">${installed || '<p class="muted">No pipes installed.</p>'}</div>
      ${library ? section("Library", `<table class="table"><tbody>${library}</tbody></table>`) : ""}
      ${section("New pipe", '<form class="stack" data-form="pipe"><div class="inline"><input name="name" placeholder="name-like-this" required /><input name="schedule" placeholder="daily 17:00" value="daily 17:00" /></div><textarea name="prompt" placeholder="What should the report say?" required></textarea><div class="actions"><button class="btn primary" type="submit">Install</button></div></form>')}`;
  } else {
    const cards = (state.workflows?.data || [])
      .map((item) => `<article class="panel-box"><header class="box-head"><h2>${esc(item.title)}</h2><span class="num faint">score ${esc(item.score)}</span></header><p class="muted">${esc(item.description || "")}</p><p class="meta">${esc((item.apps || []).join(", "))} · ${item.minutes || 0} min</p><p>${esc(item.automation || "")}</p><p class="snippet">${esc(item.evidence || "")}</p><div class="actions"><button class="btn sm" type="button" data-act="workflow" data-id="${item.id}">Install as a pipe</button></div></article>`)
      .join("");
    body = `<p class="muted">Loops that repeat in the recorded frames. Hand one to an agent with the <code>learn_from_recording</code> MCP prompt, or install it as a scheduled pipe.</p><div class="card-grid">${cards || '<p class="muted">No repeated workflow yet.</p>'}</div>`;
  }
  return `<div class="stack"><nav class="tabs">${tabs}</nav>${body}</div>`;
}

/* Integrations */

function integrationsHtml() {
  const data = state.clients || { data: [] };
  const groups = { stdio: [], http: [] };
  for (const item of data.data) groups[item.transport].push(item);
  const tile = (item) => `<button class="tile click ${state.client?.id === item.id ? "is-on" : ""}" type="button" data-act="client" data-id="${esc(item.id)}"><h3>${esc(item.label)}</h3><span class="pill ${item.detected ? "ok" : ""}">${item.detected ? "detected" : item.transport === "http" ? "remote" : "local"}</span></button>`;
  const selected = state.client;
  const detail = selected
    ? section(
        selected.label,
        `${selected.path ? `<p class="meta"><code>${esc(selected.path)}</code></p>` : ""}
        <pre>${esc(selected.snippet)}</pre>
        <ol class="steps-list">${(selected.steps || []).map((step) => `<li>${esc(step)}</li>`).join("")}</ol>
        <p class="faint">Install merges this server into the client's config and keeps a <code>.chronicle-backup</code> copy of the old file. Same as <code>chronicle mcp install ${esc(selected.id)}</code>.</p>`,
        `<div class="actions tight"><button class="btn sm" type="button" data-act="copy-snippet">Copy</button>${(data.data.find((item) => item.id === selected.id) || {}).installable ? `<button class="btn sm primary" type="button" data-act="client-install" data-id="${esc(selected.id)}">Install</button>` : ""}</div>`,
      )
    : "";
  const packages = PACKAGES.map((item) => `<article class="tile"><h3>${esc(item.name)}</h3><p class="faint">${esc(item.body)}</p><pre class="cmd">${esc(item.cmd)}</pre><button class="btn sm" type="button" data-act="copy-text" data-text="${esc(item.cmd)}">Copy</button></article>`).join("");
  const source = parseSource(state.exportSource);
  const href = (format) => media(`/contexts/export${qs({ ...source, preset: state.exportPreset, format })}`);
  const destinations = DESTINATIONS.map((item) => `<tr><td><strong>${esc(item.name)}</strong><br><span class="faint">${esc(item.how)}</span></td><td><span class="kind">${esc(item.format)}</span></td><td class="right"><a class="btn sm" href="${href(item.format)}" download>Download</a></td></tr>`).join("");
  return `<div class="stack">
    ${section("Agent providers over MCP", `<p class="muted">Chronicle is an MCP server. Local clients start <code>chronicle mcp</code> over stdio. Remote clients (ChatGPT, the OpenAI and Anthropic APIs) reach <code>${esc(data.mcp_url || "/mcp")}</code>, which must be public HTTPS behind a tunnel with <code>CHRONICLE_TOKEN</code> set.</p>
      <p class="label">On this computer</p><div class="tile-grid">${groups.stdio.map(tile).join("")}</div>
      <p class="label">Remote and APIs</p><div class="tile-grid">${groups.http.map(tile).join("")}</div>`)}
    ${detail}
    ${section("Packaged installs", `<p class="muted">One-step installs that need <a href="https://docs.astral.sh/uv/" target="_blank" rel="noreferrer">uv</a>. Each ships instructions telling the model to call <code>get_context</code> first.</p><div class="tile-grid wide">${packages}</div>`)}
    ${section(
      "Context as an input",
      `<p class="muted">Platforms without MCP can still take Chronicle's context as a file. Pick what to export, then download the format each platform accepts.</p>
      <div class="inline"><label class="field">From<select data-change="export-source">${sourceOptions(state.exportSource)}</select></label><label class="field">Workflow<select data-change="export-preset">${presetOptions(state.exportPreset)}</select></label></div>
      <table class="table"><tbody>${destinations}</tbody></table>`,
    )}
  </div>`;
}

/* Settings */

function toggle(name, label, on) {
  return `<label class="check"><input type="checkbox" name="${name}" ${on ? "checked" : ""} /> ${esc(label)}</label>`;
}

function settingsHtml() {
  const s = state.settings || {};
  const ai = state.ai || {};
  return `<div class="stack narrow-page">
    ${section(
      "Capture",
      `<form class="stack" data-form="capture-settings">
        <div class="inline">
          <label class="field">Seconds between samples <input name="capture_interval_secs" type="number" min="1" max="600" value="${esc(s.capture_interval_secs)}" /></label>
          <label class="field">Idle after (seconds) <input name="idle_threshold_secs" type="number" min="15" value="${esc(s.idle_threshold_secs)}" /></label>
        </div>
        <div class="checks">${toggle("capture_screenshots", "Screenshots", s.capture_screenshots)}${toggle("capture_ocr", "OCR when tesseract is installed", s.capture_ocr)}${toggle("capture_clipboard", "Clipboard changes", s.capture_clipboard)}${toggle("redact", "Redact emails and secrets", s.redact)}</div>
        <div class="actions"><button class="btn primary" type="submit">Save capture settings</button></div>
      </form>`,
    )}
    ${section(
      "Retention",
      `<form class="inline" data-form="retention"><input name="days" class="narrow" type="number" min="0" value="${esc(s.retention_days)}" /><button class="btn" type="submit">Keep this many days</button></form>
      <p class="faint">0 keeps everything. A shorter window deletes older frames now.</p>`,
    )}
    ${section(
      "Model for Ask and pipes",
      `<p class="muted">${ai.configured ? `Using ${esc(ai.model)} at ${esc(ai.base_url)}.` : "Answers come from local history. Add any OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama, LM Studio) to write them with a model."}</p>
      <form class="stack" data-form="ai">
        <input name="ai_base_url" placeholder="http://localhost:11434/v1" value="${esc(s.ai_base_url || "")}" />
        <input name="ai_model" placeholder="model name" value="${esc(s.ai_model || "")}" />
        <input name="ai_api_key" type="password" placeholder="${s.ai_api_key_set ? "Key saved. Type to replace" : "API key (blank for local servers)"}" autocomplete="off" />
        <div class="actions"><button class="btn" type="submit">Save model</button></div>
      </form>`,
    )}
    ${section(
      "Data",
      `<p class="muted">Everything lives in <code>${esc(s.home || "~/.chronicle")}</code>. Database ${bytes(state.health?.db_bytes)}. API docs at <a href="/docs">/docs</a>.</p>
      <div class="actions"><button class="btn" type="button" data-act="demo">Load sample workday</button><button class="btn danger" type="button" data-act="demo-replace">Replace history with the sample</button></div>`,
    )}
  </div>`;
}

/* Painting */

function viewHtml() {
  const views = {
    record: recordHtml,
    contexts: contextsHtml,
    workflows: workflowsHtml,
    integrations: integrationsHtml,
    settings: settingsHtml,
  };
  return (views[state.view] || homeHtml)();
}

function paintView() {
  const view = $("#view");
  if (view) view.innerHTML = viewHtml();
  syncChrome();
}

function paint() {
  const root = $("#app");
  if (!state.ready || !state.settings) {
    root.innerHTML = bootHtml();
    return;
  }
  if (!state.settings.onboarded) {
    root.innerHTML = onboardingHtml();
    return;
  }
  if (!$(".shell")) root.innerHTML = shellHtml();
  paintView();
}

/* Drawers */

function frameDrawer(bundle) {
  const frame = bundle.frame;
  const shot = frame.media_url ? `<img src="${esc(media(frame.media_url))}" alt="${esc(frame.app_name)} ${esc(frame.window_name)}" />` : "";
  const tags = (bundle.tags || []).map((tag) => `<span class="tag">${esc(tag)}</span>`).join("");
  const events = (bundle.events || [])
    .map((event) => `<div class="line"><span class="kind">${esc(event.event_type)}</span> ${esc(clock(event.timestamp))} · ${esc(snippet(event.text, 160))}</div>`)
    .join("");
  const elements = (bundle.elements || [])
    .map((element) => `<div class="line"><span class="kind">${esc(element.role)}</span> ${esc(element.text)}</div>`)
    .join("");
  const audio = (bundle.audio || [])
    .map((line) => `<div class="line"><span class="who">${esc(line.speaker_name || "Audio")}</span><div>${esc(line.transcription)}</div></div>`)
    .join("");
  return `<div class="backdrop" data-act="close"><aside class="sheet" data-sheet>
    <header><div><p class="label">${esc(stamp(frame.timestamp))} · frame ${frame.frame_id}${frame.session_id ? ` · recording ${frame.session_id}` : ""}</p><h2>${esc(frame.app_name)} · ${esc(frame.window_name)}</h2></div><button class="btn sm" type="button" data-act="close">Close</button></header>
    ${frame.browser_url ? `<p class="meta"><a href="${esc(frame.browser_url)}" target="_blank" rel="noreferrer">${esc(frame.browser_url)}</a></p>` : ""}
    ${shot}
    <p class="snippet">${esc(frame.text || "")}</p>
    <div class="tags">${tags}</div>
    ${events ? `<p class="label">System events</p>${events}` : ""}
    ${elements ? `<p class="label">Elements</p>${elements}` : ""}
    ${audio ? `<p class="label">Audio nearby</p>${audio}` : ""}
    <div class="actions"><button class="btn sm" type="button" data-act="memory">Save as a memory</button></div>
  </aside></div>`;
}

function logsDrawer(name, payload) {
  const runs = payload.executions || [];
  const body = runs.length
    ? runs.map((run) => `<article class="line"><strong>${esc(run.status)}</strong> · ${esc(stamp(run.started_at))}<pre>${esc(run.stdout || run.stderr || "")}</pre></article>`).join("")
    : `<p class="muted">No runs yet.</p>`;
  return `<div class="backdrop" data-act="close"><aside class="sheet" data-sheet>
    <header><h2>${esc(name)}</h2><button class="btn sm" type="button" data-act="close">Close</button></header>
    ${body}
  </aside></div>`;
}

function textDrawer(title, text, meta) {
  return `<div class="backdrop" data-act="close"><aside class="sheet" data-sheet>
    <header><div><p class="label">${esc(meta || "")}</p><h2>${esc(title)}</h2></div><button class="btn sm" type="button" data-act="close">Close</button></header>
    <p class="snippet">${esc(text || "")}</p>
  </aside></div>`;
}

async function openFrame(id) {
  const bundle = await api(`/frames/${id}/context`);
  state.bundle = bundle;
  const drawer = $("#drawer");
  if (!drawer) return;
  drawer.innerHTML = frameDrawer(bundle);
}

/* Ask */

async function streamAsk(question) {
  const cleaned = question.trim();
  if (!cleaned) return;
  const box = $("#answer");
  if (!box) return;
  box.hidden = false;
  box.replaceChildren();
  const pending = document.createElement("p");
  pending.className = "muted";
  pending.textContent = "Looking through the local history…";
  box.appendChild(pending);
  const headers = { "Content-Type": "application/json" };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const response = await fetch("/ask/stream", { method: "POST", headers, body: JSON.stringify({ question: cleaned }) });
  if (!response.ok || !response.body) {
    const error = await response.json().catch(() => ({ error: "Ask failed" }));
    throw new Error(error.error || "Ask failed");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  let mode = "";
  let citations = [];
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const payload = JSON.parse(line.slice(6));
      if (payload.delta) answer += payload.delta;
      if (payload.done) {
        mode = payload.mode || mode;
        citations = payload.citations || citations;
        if (payload.answer) answer = payload.answer;
      }
    }
    renderAnswer(answer, citations, mode);
  }
}

function renderAnswer(text, citations, mode) {
  const box = $("#answer");
  if (!box) return;
  box.hidden = false;
  box.replaceChildren();
  const body = document.createElement("div");
  body.className = "answer-body";
  body.textContent = text || "";
  box.appendChild(body);
  if (mode) {
    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = mode === "model" ? "Model, grounded in the local notes." : "From local history.";
    box.appendChild(meta);
  }
  if (!citations?.length) return;
  const row = document.createElement("div");
  row.className = "tags";
  for (const cite of citations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tag click";
    button.dataset.act = "citation";
    button.dataset.type = cite.type || "";
    button.dataset.id = cite.id ?? "";
    button.dataset.frame = cite.frame_id ?? "";
    button.textContent = cite.label || "Source";
    row.appendChild(button);
  }
  box.appendChild(row);
}

async function finishOnboarding(start) {
  state.settings = await api("/settings", { method: "POST", body: JSON.stringify({ onboarded: true }) });
  if (start === "demo") {
    await api("/demo/load", { method: "POST" });
    toast("Sample workday loaded");
    await openContext("all", null, "graph");
    return;
  }
  if (start === "record") {
    await api("/sessions", { method: "POST", body: JSON.stringify({ duration: "15m", label: "First recording" }) });
    toast("Recording for 15 minutes");
    await go("record");
    return;
  }
  await go("integrations");
}

async function refreshCtx() {
  await loadContext();
  history.replaceState(null, "", hashFor());
  paintView();
}

const actions = {
  next() {
    state.step = Math.min(2, state.step + 1);
    paint();
  },
  back() {
    state.step = Math.max(0, state.step - 1);
    paint();
  },
  async finish(button) {
    await finishOnboarding(button.dataset.start);
  },
  async nav(button) {
    await go(button.dataset.view);
  },
  async frame(button) {
    await openFrame(button.dataset.id);
  },
  close(button, event) {
    if (event?.target?.closest("[data-sheet]") && !event.target.closest("button")) return;
    closeDrawer();
  },
  jump(button) {
    document.getElementById(button.dataset.target)?.scrollIntoView({ behavior: "smooth" });
  },
  async "open-session"(button) {
    await openContext("session", Number(button.dataset.id), button.dataset.tab || "files");
  },
  async "open-saved"(button) {
    await openContext("saved", Number(button.dataset.id));
  },
  async "open-all"() {
    await openContext("all", null);
  },
  async "saved-delete"(button, event) {
    event?.stopPropagation();
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Delete?";
      return;
    }
    await api(`/contexts/${button.dataset.id}`, { method: "DELETE" });
    state.saved = (await api("/contexts")).data;
    paintView();
    toast("Context deleted");
  },
  async "ctx-tab"(button) {
    state.ctx.tab = button.dataset.tab;
    if (state.ctx.tab === "graph" && !state.ctx.graph) await loadCtxGraph();
    if (state.ctx.tab === "meetings" && !state.speakers) state.speakers = await api("/speakers/unnamed");
    history.replaceState(null, "", hashFor());
    paintView();
  },
  async "ctx-preview"(button) {
    const ctx = state.ctx;
    const preset = button.dataset.preset;
    if (ctx.preview?.preset === preset) {
      ctx.preview = null;
    } else {
      const text = await api(ctxExport({ preset }), { raw: true });
      ctx.preview = { preset, text };
    }
    paintView();
  },
  "ctx-copy"() {
    if (state.ctx?.preview) copy(state.ctx.preview.text);
  },
  "meeting-notes"(button) {
    const meeting = state.ctx.detail.meetings[Number(button.dataset.index)];
    if (!meeting) return;
    const transcript = (meeting.segments || []).map((s) => `- ${s.speaker_name || "Unnamed speaker"}: ${s.transcription}`).join("\n");
    download(`${(meeting.title || "meeting").toLowerCase().replace(/[^a-z0-9]+/g, "-")}-notes.md`, `${meeting.notes_markdown}\n\n### Transcript\n${transcript}\n`);
  },
  "event-type"(button) {
    state.ctx.eventType = button.dataset.type;
    paintView();
  },
  async prompt(button) {
    const input = document.querySelector("[name=question]");
    if (input) input.value = button.dataset.q;
    await streamAsk(button.dataset.q);
  },
  async citation(button) {
    const type = button.dataset.type;
    const frameId = button.dataset.frame || (["frame", "accessibility", "ocr"].includes(type) ? button.dataset.id : "");
    if (frameId) await openFrame(frameId);
    else if (type === "workflow") await go("workflows");
  },
  async result(button) {
    const item = state.ctx?.find?.content?.[Number(button.dataset.index)];
    if (!item) return;
    const content = item.content || {};
    if (content.frame_id && item.type !== "Input") {
      await openFrame(content.frame_id);
      return;
    }
    const drawer = $("#drawer");
    if (drawer) drawer.innerHTML = textDrawer(content.app_name || content.speaker_name || item.type, content.text, stamp(content.timestamp));
  },
  async memory() {
    const frame = state.bundle?.frame;
    if (!frame) return;
    await api("/memories", {
      method: "POST",
      body: JSON.stringify({ content: String(frame.text || "").slice(0, 4000), source: "user", frame_id: frame.frame_id }),
    });
    toast("Saved to memories");
  },
  async "session-stop"(button) {
    button.disabled = true;
    button.textContent = "Building graph…";
    const done = await api(`/sessions/${button.dataset.id}/stop`, { method: "POST" });
    await loadView("record");
    paint();
    toast(`Recording ${done.id} stopped · ${done.frames} frames`);
  },
  async "session-delete"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Delete frames?";
      return;
    }
    await api(`/sessions/${button.dataset.id}`, { method: "DELETE" });
    await loadView("record");
    paint();
    toast("Recording deleted");
  },
  async node(button) {
    const ctx = state.ctx;
    ctx.node = await api(`/graph/node${qs({ id: button.dataset.id, session_id: ctxGraphSession() })}`);
    paintView();
  },
  async "search-node"(button) {
    const ctx = state.ctx;
    ctx.tab = "graph";
    if (!ctx.graph) await loadCtxGraph();
    ctx.node = await api(`/graph/node${qs({ id: button.dataset.id, session_id: ctxGraphSession() })}`);
    history.replaceState(null, "", hashFor());
    paintView();
  },
  async "node-context"(button) {
    const scope = ctxScope();
    const made = await api("/contexts", { method: "POST", body: JSON.stringify({ task: button.dataset.label, session_id: scope.session_id || null }) });
    toast("Context generated");
    await openContext("saved", made.id);
  },
  "graph-kind"(button) {
    state.ctx.graphKind = button.dataset.kind;
    paintView();
  },
  async "graph-build"(button) {
    button.disabled = true;
    const session = ctxGraphSession();
    const built = await api("/graph/build", { method: "POST", body: JSON.stringify({ session_id: session ? Number(session) : null }) });
    await loadCtxGraph();
    state.ctx.node = null;
    paintView();
    toast(`Built ${built.nodes} nodes and ${built.edges} edges`);
  },
  "workflow-tab"(button) {
    state.workflowTab = button.dataset.tab;
    paintView();
  },
  async client(button) {
    state.client = await api(`/clients/${encodeURIComponent(button.dataset.id)}`);
    paintView();
  },
  "copy-snippet"() {
    if (state.client) copy(state.client.snippet);
  },
  "copy-text"(button) {
    copy(button.dataset.text);
  },
  async "client-install"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Write config?";
      return;
    }
    const result = await api(`/clients/${encodeURIComponent(button.dataset.id)}/install`, { method: "POST", body: "{}" });
    toast(result.path ? `Wrote ${result.path}. Restart ${result.restart}.` : "Installed");
    button.textContent = "Installed";
    button.disabled = true;
  },
  async demo() {
    const result = await api("/demo/load", { method: "POST" });
    toast(result.loaded ? "Sample workday loaded" : "History is not empty; use Replace in Settings");
    await loadView(state.view);
    paint();
  },
  async "demo-replace"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "This deletes all recordings. Click again";
      return;
    }
    await api("/demo/load?replace=true", { method: "POST" });
    state.ctx = null;
    await loadView(state.view);
    paint();
    toast("History replaced with the sample workday");
  },
  async "pipe-run"(button) {
    button.disabled = true;
    const result = await api(`/pipes/${encodeURIComponent(button.dataset.name)}/run`, { method: "POST" });
    state.pipes = await api("/pipes");
    paintView();
    const drawer = $("#drawer");
    if (drawer) drawer.innerHTML = logsDrawer(button.dataset.name, { executions: [result] });
    toast(result.status === "success" ? "Pipe finished" : "Pipe failed");
  },
  async "pipe-toggle"(button) {
    const enabled = button.dataset.enabled !== "1";
    await api(`/pipes/${encodeURIComponent(button.dataset.name)}/enable`, { method: "POST", body: JSON.stringify({ enabled }) });
    state.pipes = await api("/pipes");
    paintView();
  },
  async "pipe-install"(button) {
    await api("/pipes/install", { method: "POST", body: JSON.stringify({ source: `catalog:${button.dataset.name}` }) });
    state.pipes = await api("/pipes");
    paintView();
    toast("Installed, still off");
  },
  async "pipe-logs"(button) {
    const logs = await api(`/pipes/${encodeURIComponent(button.dataset.name)}/logs`);
    const drawer = $("#drawer");
    if (drawer) drawer.innerHTML = logsDrawer(button.dataset.name, logs);
  },
  async workflow(button) {
    const created = await api(`/workflows/${button.dataset.id}/install-pipe`, { method: "POST" });
    state.pipes = await api("/pipes");
    toast(`Installed ${created.name}. It is off until you schedule it.`);
  },
};

const forms = {
  async ask(form) {
    await streamAsk(String(new FormData(form).get("question") || ""));
  },
  async record(form) {
    const data = new FormData(form);
    const duration = String(data.get("duration") || "30m");
    await api("/sessions", {
      method: "POST",
      body: JSON.stringify({
        duration: duration === "open" ? null : duration,
        label: String(data.get("label") || "") || null,
        interval_secs: Number(data.get("interval")) || null,
      }),
    });
    await loadView("record");
    paint();
    toast(duration === "open" ? "Recording until you stop it" : `Recording for ${duration}`);
  },
  async "new-context"(form) {
    const data = new FormData(form);
    state.newContext = { task: String(data.get("task") || ""), source: String(data.get("source") || ""), preset: String(data.get("preset") || "general") };
    const button = form.querySelector("button[type=submit]");
    if (button) {
      button.disabled = true;
      button.textContent = "Generating…";
    }
    const made = await api("/contexts", {
      method: "POST",
      body: JSON.stringify({ task: state.newContext.task || null, preset: state.newContext.preset, ...parseSource(state.newContext.source) }),
    });
    state.newContext.task = "";
    toast("Context generated and saved");
    await openContext("saved", made.id);
  },
  async graph(form) {
    const ctx = state.ctx;
    ctx.graphQuery = String(new FormData(form).get("q") || "");
    ctx.graphResults = ctx.graphQuery ? (await api(`/graph/search${qs({ q: ctx.graphQuery, limit: 12, session_id: ctxGraphSession() })}`)).data : null;
    paintView();
  },
  async "ctx-search"(form) {
    const ctx = state.ctx;
    ctx.findQ = String(new FormData(form).get("q") || "");
    ctx.find = await api(`/contexts/search${qs({ q: ctx.findQ, ...ctxScope() })}`);
    paintView();
  },
  async speaker(form) {
    const data = new FormData(form);
    await api("/speakers/update", { method: "POST", body: JSON.stringify({ id: Number(data.get("id")), name: String(data.get("name") || "") }) });
    state.speakers = await api("/speakers/unnamed");
    await refreshCtx();
    toast("Speaker named");
  },
  async pipe(form) {
    const data = new FormData(form);
    await api("/pipes/install", {
      method: "POST",
      body: JSON.stringify({ name: String(data.get("name") || ""), schedule: String(data.get("schedule") || "manual"), prompt: String(data.get("prompt") || "") }),
    });
    state.pipes = await api("/pipes");
    paintView();
    toast("Pipe installed");
  },
  async "capture-settings"(form) {
    const data = new FormData(form);
    const payload = {
      capture_interval_secs: Number(data.get("capture_interval_secs")) || 5,
      idle_threshold_secs: Number(data.get("idle_threshold_secs")) || 120,
      capture_screenshots: data.has("capture_screenshots"),
      capture_ocr: data.has("capture_ocr"),
      capture_clipboard: data.has("capture_clipboard"),
      redact: data.has("redact"),
    };
    state.settings = await api("/settings", { method: "POST", body: JSON.stringify(payload) });
    paint();
    toast("Capture settings saved");
  },
  async retention(form) {
    const days = Number(new FormData(form).get("days"));
    const result = await api("/retention/configure", { method: "POST", body: JSON.stringify({ days }) });
    state.settings = await api("/settings");
    state.health = await api("/health");
    toast(result.skipped ? "Keeping the full file" : `Removed ${result.frames} older frames`);
    paint();
  },
  async ai(form) {
    const data = new FormData(form);
    const payload = { ai_base_url: String(data.get("ai_base_url") || ""), ai_model: String(data.get("ai_model") || "") };
    const key = String(data.get("ai_api_key") || "").trim();
    if (key) payload.ai_api_key = key;
    state.settings = await api("/settings", { method: "POST", body: JSON.stringify(payload) });
    state.ai = await api("/ai/status");
    paint();
    toast(state.ai.configured ? "Model saved" : "Model cleared. Answers stay local.");
  },
};

const changes = {
  "export-source"(select) {
    state.exportSource = select.value;
    paintView();
  },
  "export-preset"(select) {
    state.exportPreset = select.value;
    paintView();
  },
};

document.addEventListener("click", async (event) => {
  if (event.target.closest("a[href]")) return;
  const button = event.target.closest("[data-act]");
  if (!button || button.disabled) return;
  const act = actions[button.dataset.act];
  if (!act) return;
  if (button.dataset.act === "close" && event.target.closest("[data-sheet]") && event.target.closest("button") == null) return;
  try {
    await act(button, event);
  } catch (error) {
    toast(error.message || "Request failed");
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.dataset.form) return;
  event.preventDefault();
  const handler = forms[form.dataset.form];
  if (!handler) return;
  try {
    await handler(form);
  } catch (error) {
    toast(error.message || "Request failed");
    form.querySelectorAll("button[disabled]").forEach((button) => {
      button.disabled = false;
    });
  }
});

document.addEventListener("change", (event) => {
  const handler = changes[event.target?.dataset?.change];
  if (handler) handler(event.target);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

window.addEventListener("hashchange", async () => {
  if (location.hash === hashFor()) return;
  readHash();
  closeDrawer();
  try {
    await loadView(state.view);
  } catch (error) {
    toast(error.message || "Request failed");
  }
  paint();
});

async function boot() {
  paint();
  try {
    state.settings = await api("/settings");
    state.health = await api("/health");
    if (state.settings.onboarded) {
      readHash();
      try {
        await loadView(state.view);
      } catch (error) {
        state.view = "home";
        state.ctx = null;
        await loadView("home");
        toast(error.message || "Could not open that page");
      }
    }
  } catch (error) {
    state.bootError = error.message || "The local engine is not reachable.";
  }
  state.ready = true;
  paint();
  startPolling();
}

boot();
