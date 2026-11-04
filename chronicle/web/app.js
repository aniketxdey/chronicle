const VIEWS = ["recordings", "harnesses", "contexts", "workflows", "integrations", "settings"];
const TITLES = {
  recordings: "Recordings",
  harnesses: "Harnesses",
  contexts: "Contexts",
  workflows: "Workflows",
  integrations: "Integrations",
  settings: "Settings",
};
const NAV_GROUPS = [["Build", ["recordings", "harnesses"]], ["Library", ["contexts", "workflows"]], ["Connect", ["integrations", "settings"]]];
const ICONS = {
  recordings: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/>',
  harnesses: '<path d="M12 3 21 8l-9 5-9-5z"/><path d="m3 12.5 9 5 9-5M3 17l9 5 9-5"/>',
  contexts: '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M9 12h7M9 16h7"/>',
  workflows: '<rect x="3" y="4" width="6" height="5"/><rect x="15" y="4" width="6" height="5"/><rect x="9" y="15" width="6" height="5"/><path d="M6 9v3h12V9M12 12v3"/>',
  integrations: '<path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  mini: '<rect x="3" y="5" width="18" height="14" rx="1.5"/><rect x="12" y="12" width="6" height="4" fill="currentColor"/>',
};
const RECORDING_TABS = [
  ["graph", "Knowledge graph"],
  ["contexts", "Contexts and workflows"],
  ["timeline", "Timeline"],
  ["meetings", "Meetings"],
  ["search", "Search"],
];
// Low-level kinds stay out of the graph until asked for; they remain in each node's lists.
const DETAIL_KINDS = new Set(["window", "identifier", "query", "task", "topic_cluster"]);
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
const PROMPTS = ["What was I working on?", "What's still unfinished?", "Which files and links mattered most?"];
const GRAPH_FORMATS = [["json", "JSON"], ["md", "Text"], ["graphml", "GraphML"], ["cypher", "Cypher"]];
const DESTINATIONS = [
  { name: "ChatGPT Projects and custom GPTs", how: "Upload the one file under Project files or GPT Knowledge.", format: "md" },
  { name: "Claude Projects", how: "Add the one file to the project knowledge.", format: "md" },
  { name: "Gemini Gems and NotebookLM", how: "Upload the one file as a source.", format: "md" },
  { name: "Cursor, Codex, and Claude Code", how: "Unzip into the repo, so AGENTS.md sits at the root with its files beside it.", format: "zip" },
  { name: "Obsidian and Notion", how: "Unzip into a vault or import the folder as pages.", format: "zip" },
  { name: "OpenAI and Anthropic file search", how: "Upload the unzipped files to a vector store or the Files API.", format: "zip" },
  { name: "LangChain, LlamaIndex, custom RAG", how: "Load the graph engine's nodes and edges from one JSON file.", format: "json" },
  { name: "Neo4j", how: "Paste into Neo4j Browser or run with cypher-shell.", format: "cypher" },
  { name: "Gephi, yEd, Cytoscape", how: "Open the GraphML file to lay out and explore the graph.", format: "graphml" },
];
const HARNESS_EXAMPLES = ["Recruiting", "Quant research", "Investor relations", "Customer support"];
const PACKAGES = [
  { name: "Claude plugin", body: "Claude Code, Claude Desktop, and Cowork", cmd: "claude plugin marketplace add aniketxdey/chronicle\nclaude plugin install chronicle@chronicle" },
  { name: "Gemini CLI extension", body: "Installs the server and its instructions", cmd: "gemini extensions install https://github.com/aniketxdey/chronicle" },
  { name: "MCP Registry", body: "Any client that reads the official registry", cmd: "io.github.aniketxdey/chronicle" },
  { name: "Any MCP client", body: "Runs from PyPI without installing", cmd: "uvx chronicle-context mcp" },
];

const state = {
  ready: false,
  view: "recordings",
  landing: false,
  step: 0,
  settings: null,
  health: null,
  harnesses: null,
  wizard: null,
  ide: null,
  palette: null,
  sessions: null,
  capture: null,
  saved: null,
  presets: null,
  ctx: null,
  doc: null,
  flow: null,
  chats: {},
  workflows: null,
  clients: null,
  client: null,
  exportHarness: "",
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

function inlineMd(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])_([^_]+)_(?=[\s).,]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/(^|\s)(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noreferrer">$2</a>')
    .replace(/\(frames? (\d+)\)/g, '(<button class="link cite" type="button" data-act="frame" data-id="$1">frame $1</button>)');
}

// Model-written Markdown, escaped first. Covers what context files and answers use.
function mdHtml(text) {
  const out = [];
  let list = null;
  let para = [];
  let code = null;
  const flush = () => {
    if (para.length) out.push(`<p>${inlineMd(para.join(" "))}</p>`);
    para = [];
  };
  const close = () => {
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (const raw of String(text || "").split("\n")) {
    if (code) {
      if (raw.startsWith("```")) {
        out.push(`<pre>${esc(code.join("\n"))}</pre>`);
        code = null;
      } else code.push(raw);
      continue;
    }
    const line = raw.trim();
    const heading = line.match(/^(#{1,5}) (.*)/);
    const item = raw.match(/^(\s*)(?:[-*]|(\d+)\.) (.*)/);
    if (line.startsWith("```")) {
      flush();
      close();
      code = [];
    } else if (!line) {
      flush();
      close();
    } else if (heading) {
      flush();
      close();
      out.push(`<h${Math.min(heading[1].length + 1, 5)}>${inlineMd(heading[2])}</h${Math.min(heading[1].length + 1, 5)}>`);
    } else if (/^(-{3,}|\*{3,})$/.test(line)) {
      flush();
      close();
      out.push("<hr />");
    } else if (line.startsWith("> ")) {
      flush();
      close();
      out.push(`<blockquote>${inlineMd(line.slice(2))}</blockquote>`);
    } else if (item) {
      flush();
      const kind = item[2] ? "ol" : "ul";
      if (!item[1] && list !== kind) {
        close();
        out.push(`<${kind}>`);
        list = kind;
      } else if (!list) {
        out.push("<ul>");
        list = "ul";
      }
      out.push(`<li${item[1] ? ' class="sub"' : ""}>${inlineMd(item[3])}</li>`);
    } else {
      para.push(line);
    }
  }
  flush();
  close();
  return out.join("");
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
    if (response.status === 401) throw new Error("This server needs a token, so open it once with ?token=YOUR_TOKEN");
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
  const rank = (state.ctx?.detail?.apps || []).findIndex((item) => item.app_name === app);
  return SHADES[rank < 0 ? SHADES.length - 1 : Math.min(rank, SHADES.length - 1)];
}

function closeDrawer() {
  const drawer = $("#drawer");
  if (drawer) drawer.innerHTML = "";
}

/* Routing */

function hashFor() {
  if (state.view === "recordings" && state.ctx) return `#recordings/${state.ctx.kind === "all" ? "all" : state.ctx.id}/${state.ctx.tab}`;
  if (state.view === "harnesses" && state.wizard) return "#harnesses/new";
  if (state.view === "harnesses" && state.ide) return `#harnesses/${state.ide.id}`;
  if (state.view === "contexts" && state.doc) return `#contexts/${state.doc.id}`;
  if (state.view === "workflows" && state.flow) return `#workflows/${state.flow.id}`;
  return `#${state.view}`;
}

function readHash() {
  const parts = location.hash.replace("#", "").split("/").filter(Boolean);
  const view = VIEWS.includes(parts[0]) ? parts[0] : "recordings";
  state.view = view;
  state.landing = !parts.length;
  state.ctx = null;
  state.doc = null;
  state.flow = null;
  state.ide = null;
  state.wizard = null;
  if (view === "harnesses" && parts[1] === "new") state.wizard = newWizard();
  if (view === "harnesses" && Number(parts[1])) state.ide = newIde(Number(parts[1]));
  if (view === "recordings" && parts[1]) state.ctx = parts[1] === "all" ? newCtx("all", null, parts[2]) : newCtx("session", Number(parts[1]), parts[2]);
  if (view === "contexts" && Number(parts[1])) state.doc = { id: Number(parts[1]) };
  if (view === "workflows" && Number(parts[1])) state.flow = { id: Number(parts[1]) };
}

function newCtx(kind, id, tab) {
  const valid = RECORDING_TABS.some(([key]) => key === tab);
  return { kind, id, tab: valid ? tab : "graph", eventType: "", graphKind: "" };
}

/* Loading */

async function loadSessions(limit = 100) {
  state.sessions = await api(`/sessions?limit=${limit}`);
}

function ctxScope() {
  return state.ctx?.kind === "session" ? { session_id: state.ctx.id } : {};
}

function ctxGraphSession() {
  return ctxScope().session_id || "";
}

async function loadContext() {
  const ctx = state.ctx;
  ctx.record = ctx.kind === "session" ? await api(`/sessions/${ctx.id}`) : null;
  ctx.detail = await api(`/contexts/detail${qs(ctxScope())}`);
  await loadArtifacts();
  if (!state.presets) state.presets = (await api("/contexts/presets")).data;
  if (ctx.tab === "graph") await loadCtxGraph();
  if (ctx.tab === "meetings" && !state.speakers) state.speakers = await api("/speakers/unnamed");
}

async function loadArtifacts() {
  const ctx = state.ctx;
  if (ctx.kind === "session") {
    ctx.artifacts = await api(`/sessions/${ctx.id}/artifacts`);
    return;
  }
  const [saved, flows] = await Promise.all([api("/contexts?limit=500"), api("/workflows")]);
  ctx.artifacts = { contexts: saved.data, workflows: flows.data };
}

async function loadCtxGraph() {
  const ctx = state.ctx;
  const session = ctxGraphSession();
  ctx.graph = await api(`/graph${qs({ limit: 160, session_id: session })}`);
  ctx.graphSummary = await api(`/graph/summary${qs({ session_id: session })}`);
  state.llm = await api("/llm/status");
}

async function loadView(view) {
  if (view === "recordings") {
    await loadSessions(100);
    if (state.landing) {
      state.landing = false;
      const latest = (state.sessions.data || []).find((item) => item.frames > 0);
      if (latest) {
        state.ctx = newCtx("session", latest.id, "graph");
        history.replaceState(null, "", hashFor());
      }
    }
    if (state.ctx) await loadContext();
    else state.capture = await api("/capture/status");
  }
  if (view === "harnesses") {
    state.llm = await api("/llm/status");
    if (state.wizard) await loadWizard();
    else if (state.ide) await loadIde();
    else state.harnesses = (await api("/harnesses")).data;
  }
  if (view === "contexts") {
    await loadSessions(100);
    const listing = await api("/contexts");
    state.saved = listing.data;
    state.presets = listing.presets;
    if (state.doc) {
      const flows = (await api("/workflows")).data;
      const record = await api(`/contexts/${state.doc.id}`);
      state.doc = { ...record, refs: flows.filter((flow) => flow.sources.context_ids.includes(record.id) || (record.composed ? record.sources.workflow_ids.includes(flow.id) : record.session_id && flow.sources.session_ids.includes(record.session_id))) };
    }
  }
  if (view === "workflows") {
    state.llm = await api("/llm/status");
    if (state.flow) {
      await loadSessions(100);
      state.saved = (await api("/contexts")).data;
      state.flow = await api(`/workflows/${state.flow.id}`);
    } else state.workflows = await api("/workflows");
  }
  if (view === "integrations") {
    if (!state.clients) state.clients = await api("/clients");
    state.harnesses = (await api("/harnesses")).data;
  }
  if (view === "settings") {
    state.llm = await api("/llm/status");
    state.settings = await api("/settings");
  }
  state.health = await api("/health");
  if (view === "recordings" && !state.ctx) await loadLiveStats();
}

async function loadLiveStats() {
  const id = state.health?.recording?.id;
  state.liveStats = id ? await api(`/sessions/${id}/stats`) : null;
}

function startPolling() {
  stopPolling();
  state.poll = setInterval(async () => {
    try {
      const before = state.health?.recording?.id || null;
      state.health = await api("/health");
      const after = state.health.recording?.id || null;
      if (state.view === "recordings" && !state.ctx) {
        await loadLiveStats();
        if (before !== after) {
          await loadSessions(100);
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
  state.ctx = null;
  state.doc = null;
  state.flow = null;
  state.ide = null;
  state.wizard = null;
  Object.assign(state, extra);
  state.view = view;
  history.replaceState(null, "", hashFor());
  await loadView(view);
  paint();
  $(".main")?.scrollTo(0, 0);
}

async function openRecording(id, tab = "graph") {
  await go("recordings", { ctx: newCtx(id ? "session" : "all", id, tab) });
}

async function openDoc(id) {
  await go("contexts", { doc: { id } });
}

async function openFlow(id) {
  await go("workflows", { flow: { id } });
}

/* Shell */

function bootHtml() {
  return `<div class="boot"><p>${esc(state.bootError || "Opening Chronicle…")}</p></div>`;
}

function onboardingHtml() {
  const steps = [
    {
      title: "Give agents context from your computer",
      body: "Chronicle records your screen and system activity, writes context files and workflows from it, and lets you build them into agent harnesses that plug into any AI.",
    },
    {
      title: "Your history stays in one folder",
      body: "Screenshots, window titles, URLs, and clipboard text are saved to ~/.chronicle, and keystrokes are never recorded.",
    },
  ];
  const marks = [0, 1, 2].map((index) => `<i class="${index <= state.step ? "is-on" : ""}"></i>`).join("");
  if (state.step < 2) {
    const step = steps[state.step];
    return `<section class="onboard"><div class="panel">
      <div class="steps">${marks}</div>
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
    <h1>How do you want to begin?</h1>
    <p class="lead">Recording on macOS requires Screen Recording and Accessibility permissions.</p>
    <div class="tiles">
      <article class="tile"><h3>Record 15 minutes</h3><button class="btn primary" type="button" data-act="finish" data-start="record">Start recording</button></article>
      <article class="tile"><h3>Sample workday</h3><button class="btn" type="button" data-act="finish" data-start="demo">Load sample</button></article>
      <article class="tile"><h3>Start empty</h3><button class="btn" type="button" data-act="finish" data-start="empty">Open Chronicle</button></article>
    </div>
    <div class="actions"><button class="btn" type="button" data-act="back">Back</button></div>
  </div></section>`;
}

function shellHtml() {
  const nav = NAV_GROUPS.map(([label, views]) => `<div class="nav-group"><p class="nav-label">${label}</p>${views.map((view) => `<button class="nav-btn" type="button" data-act="nav" data-view="${view}" title="${TITLES[view]}">${icon(view)}<span>${TITLES[view]}</span></button>`).join("")}</div>`).join("");
  return `<div class="shell">
    <aside class="side">
      <div class="brand"><img class="logo" src="/static/icon.svg?v=2" alt="" /><div><strong>Chronicle</strong><span>your context for every agent</span></div></div>
      <nav class="nav">${nav}</nav>
      <div class="side-rec">
        <button class="nav-btn" type="button" data-act="mini-open" title="Open mini window">${icon("mini")}<span>Mini window</span></button>
        <button class="rec" type="button" data-act="nav" data-view="recordings" title="Recording status"><i id="rec-dot" class="dot"></i><span id="rec-label">Not recording</span></button>
      </div>
    </aside>
    <main class="main">
      <header class="head"><div id="crumbs" class="crumbs"></div><button class="kbd-btn" type="button" data-act="palette-open"><span>Go to anything</span><kbd>⌘K</kbd></button></header>
      <div id="view" class="page"></div>
    </main>
  </div>
  <footer class="statusbar"><span id="status-model"></span><span id="status-mcp"></span><span class="grow"></span><span id="graph-count"></span><span id="frame-count"></span><a href="/docs">API</a></footer>
  <div id="drawer"></div>
  <div id="palette"></div>`;
}

function crumbs() {
  const parts = [`<button type="button" data-act="nav" data-view="${state.view}">${esc(TITLES[state.view])}</button>`];
  const here = state.view === "recordings" && state.ctx ? ctxTitle() : state.view === "contexts" ? state.doc?.title : state.view === "workflows" ? state.flow?.title : state.view === "harnesses" ? (state.wizard ? "New harness" : state.ide?.data?.name) : null;
  if (here) parts.push(`<span>${esc(here)}</span>`);
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
  syncMini();
  document.querySelector(".shell")?.classList.toggle("is-ide", Boolean(state.view === "harnesses" && state.ide));
  const modelNode = $("#status-model");
  if (modelNode) modelNode.innerHTML = state.llm?.enabled ? `<i class="led on"></i>${esc(state.llm.model)}` : '<i class="led"></i>no model';
  const mcpNode = $("#status-mcp");
  if (mcpNode) mcpNode.textContent = `mcp ${location.host}/mcp`;
  const count = $("#frame-count");
  if (count && state.health) count.textContent = `${state.health.frames} frames · ${bytes(state.health.db_bytes)}`;
  const graph = $("#graph-count");
  if (graph && state.health) graph.textContent = `${state.health.graph_nodes} nodes · ${state.health.graph_edges} edges`;
  const trail = $("#crumbs");
  if (trail) trail.innerHTML = crumbs();
}

function emptyHtml() {
  return `<section class="panel-box hero">
    <h2>Nothing recorded yet</h2>
    
    <div class="actions">
      <button class="btn" type="button" data-act="demo">Load sample workday</button>
      <button class="btn" type="button" data-act="nav" data-view="integrations">Connect an agent</button>
    </div>
  </section>`;
}

function section(title, body, extra = "") {
  return `<section class="panel-box"><header class="box-head"><h2>${esc(title)}</h2>${extra}</header>${body}</section>`;
}

function pageHead(title, sub, actions = "") {
  return `<header class="page-head"><div><h1>${esc(title)}</h1>${sub ? `<p>${esc(sub)}</p>` : ""}</div>${actions ? `<div class="actions tight">${actions}</div>` : ""}</header>`;
}

function stat(label, value) {
  return `<div class="stat"><span class="label">${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

/* Recordings */

function recordingsHtml() {
  if (state.ctx) return recordingHtml();
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
        
        <div class="actions"><button class="btn stop" type="button" data-act="session-stop" data-id="${active.id}">Stop and build graph</button></div>
      </section>`
    : section(
        "New recording",
        `
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
      (item) => `<tr class="${item.status === "recording" ? "" : "click"}" ${item.status === "recording" ? "" : `data-act="open-recording" data-id="${item.id}"`}><td><strong>${esc(item.label || `Recording ${item.id}`)}</strong></td><td>${esc(stamp(item.started_at))}</td><td class="num">${esc(item.elapsed)}</td><td class="num">${item.frames}</td><td class="num">${item.events}</td><td class="num">${item.contexts || 0}</td><td><span class="pill ${item.status === "recording" ? "live" : ""}">${esc(item.status)}</span></td><td class="right">${
        item.status === "recording"
          ? `<button class="btn sm stop" type="button" data-act="session-stop" data-id="${item.id}">Stop</button>`
          : `<button class="btn sm" type="button" data-act="session-delete" data-id="${item.id}">Delete</button>`
      }</td></tr>`,
    )
    .join("");
  const all = state.health?.frames ? `<tr class="click" data-act="open-recording" data-id=""><td><strong>All history</strong></td><td></td><td></td><td class="num">${state.health.frames}</td><td></td><td></td><td></td><td></td></tr>` : "";
  return `<div class="stack">
    ${pageHead("Recordings")}
    ${live}
    ${active ? `<div class="stats live-stats" id="live-stats">${liveStatsHtml()}</div>` : ""}
    ${capture.last_error ? `<p class="banner">${esc(capture.last_error)}</p>` : ""}
    ${state.health?.frames ? "" : emptyHtml()}
    ${state.health?.frames ? section("Recordings", `<table class="table"><thead><tr><th>Recording</th><th>Started</th><th class="num">Length</th><th class="num">Frames</th><th class="num">Events</th><th class="num">Contexts</th><th>Status</th><th></th></tr></thead><tbody>${rows}${all}</tbody></table>`) : ""}
    ${section(
      "This machine",
      `<div class="pills">${capRow}</div>
      <p class="faint">Keystrokes are never recorded and these apps are skipped: ${esc((capture.denied_apps || []).join(", "))}.</p>
      `,
    )}
  </div>`;
}

function liveStatsHtml() {
  const live = state.liveStats;
  if (!live) return "";
  return [
    ["Storage used", bytes(live.storage_bytes)],
    ["Frames", live.frames.toLocaleString()],
    ["System events", live.events.toLocaleString()],
    ["Pages", live.pages.toLocaleString()],
    ["Apps", live.apps.toLocaleString()],
    ["Graph nodes", live.graph_nodes.toLocaleString()],
  ].map(([label, value]) => stat(label, value)).join("");
}

function updateLive() {
  const panel = $("#live-stats");
  if (panel) panel.innerHTML = liveStatsHtml();
  const active = state.health?.recording;
  const clockNode = $("#live-clock");
  if (active && clockNode) clockNode.textContent = active.remaining_secs != null ? countdown(active.remaining_secs) : active.elapsed;
}

/* One recording: its graph and chat first, then its context files */

function ctxTitle() {
  const ctx = state.ctx;
  if (!ctx) return "";
  if (ctx.kind === "all") return "All history";
  return ctx.record?.label || `Recording ${ctx.id}`;
}

function recordingLabel(id) {
  if (!id) return "All history";
  const found = (state.sessions?.data || []).find((item) => item.id === id);
  return found?.label || `Recording ${id}`;
}

function recordingHtml() {
  const ctx = state.ctx;
  const detail = ctx.detail || { frames: [], events: [], meetings: [], urls: [], blocks: [], apps: [], scope: {} };
  const scope = detail.scope || {};
  const seconds = detail.apps.reduce((sum, app) => sum + app.seconds, 0);
  const counts = { contexts: (ctx.artifacts?.contexts.length || 0) + (ctx.artifacts?.workflows.length || 0), meetings: detail.meetings.length, timeline: detail.frames.length };
  const tabs = RECORDING_TABS.map(([key, label]) => `<button class="tab ${ctx.tab === key ? "is-on" : ""}" type="button" data-act="ctx-tab" data-tab="${key}">${esc(label)}${counts[key] != null ? `<span class="count">${counts[key]}</span>` : ""}</button>`).join("");
  const bodies = { graph: ctxGraphHtml, contexts: ctxBundleHtml, meetings: ctxMeetingsHtml, timeline: ctxTimelineHtml, search: ctxSearchHtml };
  return `<div class="stack">
    <section class="ctx-head">
      <div>
        <h1>${esc(ctxTitle())}</h1>
        <p class="meta">${scope.start ? `${esc(stamp(scope.start))} · ` : ""}${mins(seconds)}</p>
      </div>
      <div class="actions tight"><button class="btn" type="button" data-act="workflow-new" data-session="${ctx.id || ""}">Make a workflow</button><button class="btn primary" type="button" data-act="harness-new" data-session="${ctx.id || ""}">Build a harness</button></div>
    </section>
    <nav class="tabs">${tabs}</nav>
    ${(bodies[ctx.tab] || ctxGraphHtml)()}
  </div>`;
}

function contextCard(item) {
  return `<article class="file-card click" data-act="open-doc" data-id="${item.id}">
    <h3>${esc(item.title)}</h3>
    <div class="tags"><span class="tag">${esc(presetTitle(item.preset))}</span></div>
  </article>`;
}

function flowCard(item) {
  const spec = item.spec || item;
  return `<article class="flow-card click" data-act="open-flow" data-id="${item.id}">
    <h3>${esc(item.title)}</h3>
    <p class="muted">${esc(spec.summary)}</p>
  </article>`;
}

function ctxBundleHtml() {
  const ctx = state.ctx;
  const found = ctx.artifacts || { contexts: [], workflows: [] };
  const files = found.contexts.map(contextCard).join("");
  const flows = found.workflows.map(flowCard).join("");
  const generate = `<span class="actions tight">${ctx.kind === "session" ? `<button class="btn sm" type="button" data-act="recording-generate">Generate from this recording</button>` : ""}<button class="btn sm" type="button" data-act="context-new" data-session="${ctx.id || ""}">New context file</button></span>`;
  return `<div class="stack">
    ${section(
      "Context files",
      `${files ? `<div class="file-grid">${files}</div>` : '<p class="muted">No context files yet.</p>'}`,
      generate,
    )}
    ${section(
      "Workflows",
      flows ? `<div class="flow-grid">${flows}</div>` : '<p class="muted">No workflows yet.</p>',
      `<button class="btn sm" type="button" data-act="workflow-new" data-session="${ctx.id || ""}">Make a workflow</button>`,
    )}
  </div>`;
}

function presetTitle(id) {
  return (state.presets || []).find((item) => item.id === id)?.title || id || "General";
}

/* Context files */

function graphLinks(kind, id) {
  return GRAPH_FORMATS.map(([format, label]) => `<a class="btn sm" href="${media(`/graph/download/${kind}/${id}${qs({ format })}`)}" download>${label}</a>`).join("");
}

function sourceLabel(item) {
  const { session_ids: sessions = [], context_ids: files = [], workflow_ids: flows = [] } = item.sources || {};
  if (sessions.length > 1) return `${sessions.length} recordings`;
  if (sessions.length === 1) return recordingLabel(sessions[0]);
  return files.length || flows.length ? "Files you chose" : "All history";
}

function contextsHtml() {
  if (state.doc) return docHtml();
  if (!state.health?.frames) return `<div class="stack">${emptyHtml()}</div>`;
  const rows = (state.saved || [])
    .map((item) => `<tr class="click" data-act="open-doc" data-id="${item.id}"><td><strong>${esc(item.title)}</strong></td><td><span class="tag">${esc(presetTitle(item.preset))}</span></td><td>${esc(sourceLabel(item))}</td><td>${esc(stamp(item.created_at))}</td></tr>`)
    .join("");
  return `<div class="stack">
    ${pageHead("Contexts", "", '<button class="btn primary" type="button" data-act="context-new">Generate</button>')}
    ${section("Context files", `<table class="table"><thead><tr><th>File</th><th>Kind of work</th><th>From</th><th>Created</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">No context files yet.</td></tr>'}</tbody></table>`)}
  </div>`;
}

function contextDialog(session) {
  const recordings = (state.sessions?.data || []).filter((item) => item.frames > 0);
  const only = recordings.length === 1;
  const pick = (name, id, title, meta, on) => `<label class="pick"><input type="checkbox" name="${name}" value="${id}" ${on ? "checked" : ""} /><span><strong>${esc(title)}</strong>${meta ? `<span class="faint">${esc(meta)}</span>` : ""}</span></label>`;
  const group = (label, name, rows, empty) => `<fieldset class="picks"><legend><span class="label">${label}</span><button class="link" type="button" data-act="pick-all" data-name="${name}">All</button><button class="link" type="button" data-act="pick-none" data-name="${name}">None</button></legend><div class="pick-list">${rows || `<p class="faint">${empty}</p>`}</div></fieldset>`;
  const sessionRows = recordings.map((item) => pick("session", item.id, item.label || `Recording ${item.id}`, stamp(item.started_at), only || String(item.id) === session)).join("");
  const contextRows = (state.saved || []).map((item) => pick("context", item.id, item.title, sourceLabel(item), false)).join("");
  const flowRows = (state.workflows?.data || []).map((item) => pick("workflow", item.id, item.title, "", false)).join("");
  return `<div class="backdrop center" data-act="close"><form class="modal" data-sheet data-form="context-new">
    <header><h2>New context file</h2><button class="btn sm" type="button" data-act="close">Close</button></header>
    <label class="field">Name<input name="name" placeholder="Researching job companies" autocomplete="off" required /></label>
    <label class="field">Description of the work<textarea name="description" rows="3" placeholder="What is this session of work, and what should an agent know going in?"></textarea></label>
    ${group("Recordings to look at", "session", sessionRows, "No recordings yet.")}
    ${group("Earlier contexts to look at", "context", contextRows, "No context files yet.")}
    ${group("Workflows to use", "workflow", flowRows, "No workflows yet.")}
    <p class="wiz-note" id="context-note"></p>
    <div class="actions"><button class="btn primary" type="submit">Generate</button></div>
  </form></div>`;
}

function docHtml() {
  const doc = state.doc;
  const body = (doc.markdown || "").replace(/^# [^\n]*\n+/, "");
  const refs = (doc.refs || []).map((flow) => `<button class="list-row plain" type="button" data-act="open-flow" data-id="${flow.id}"><strong>${esc(flow.title)}</strong><span class="faint">${slots(flow.spec.trigger)}</span></button>`).join("");
  return `<div class="stack">
    <section class="ctx-head">
      <div>
        <h1>${esc(doc.title)}</h1>
        <p class="meta">${doc.session_id ? `<button class="link" type="button" data-act="open-recording" data-id="${doc.session_id}">${esc(recordingLabel(doc.session_id))}</button>` : esc(sourceLabel(doc))}</p>
      </div>
      <div class="actions tight"><button class="btn" type="button" data-act="workflow-new" data-context="${doc.id}" data-session="${doc.session_id || ""}">Make a workflow from this</button><a class="btn primary" href="${media(`/contexts/${doc.id}/document`)}" download>Download .md</a><button class="btn" type="button" data-act="doc-delete" data-id="${doc.id}">Delete</button></div>
    </section>
    <div class="doc-grid">
      <article class="panel-box prose">${mdHtml(body)}</article>
      <aside class="stack">
        ${graphSection("Context graph", "context", doc.id, doc.graph)}
        ${doc.node ? nodePanel() : ""}
        ${refs ? section("Workflows it refers to", `<div class="list">${refs}</div>`) : ""}
        ${doc.sources.session_ids.length + doc.sources.context_ids.length > 1 ? section("Made from", `<div class="tags">${doc.sources.session_ids.map((id) => `<button class="tag click" type="button" data-act="open-recording" data-id="${id}">${esc(recordingLabel(id))}</button>`).join("")}${doc.sources.context_ids.map((id) => `<button class="tag click" type="button" data-act="open-doc" data-id="${id}">${esc((state.saved || []).find((item) => item.id === id)?.title || `Context ${id}`)}</button>`).join("")}</div>`) : ""}
        ${agentSection("get_context_file", doc.id)}
      </aside>
    </div>
  </div>`;
}

function graphSection(title, kind, id, data) {
  return section(
    title,
    `${data?.nodes?.length ? '<canvas id="graph3d" class="graph3d small" role="img" aria-label="Knowledge graph. Drag to rotate, select a node to open it."></canvas>' : '<p class="muted">No graph nodes matched this one.</p>'}
    <div class="actions"><span class="label">Download</span>${graphLinks(kind, id)}</div>`,
  );
}

function agentSection(tool, id) {
  const call = `${tool}(id=${id})`;
  return section(
    "Give it to an agent",
    `
    <div class="inline spread"><code>${esc(call)}</code><button class="btn sm" type="button" data-act="copy-text" data-text="${esc(call)}">Copy</button></div>`,
  );
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
    ${naming ? section("Unnamed speakers", `<div class="stack">${naming}</div>`) : ""}
    ${cards || section("Meetings", '<p class="muted">No meetings.</p>')}
  </div>`;
}

function kindSwatch(kind) {
  const style = KIND_STYLE[kind] || { fill: "#808080" };
  return `<i class="swatch" style="background:${style.fill};${style.stroke ? `box-shadow:inset 0 0 0 1.5px ${style.stroke}` : ""}"></i>`;
}

/* A 3D particle simulation on a canvas: black and white, no libraries. */

const sim = { key: "", points: [], links: [], yaw: 0.6, pitch: 0.34, spin: 0, touched: 0, zoom: 1, alpha: 1, drag: null, hover: null, raf: 0, canvas: null, screen: [], maxWeight: 1 };
const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const CAMERA = 470;
const FLOOR_Y = 235;
const TAU = Math.PI * 2;
const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));

function graphHost() {
  if (state.view === "recordings" && state.ctx?.tab === "graph") return state.ctx;
  if (state.view === "contexts" && state.doc) return state.doc;
  if (state.view === "workflows" && state.flow) return state.flow;
  if (state.view === "harnesses" && state.ide) return ideGraphHost();
  return null;
}

function hostSession(host) {
  if (host === state.ctx) return ctxGraphSession();
  return host === state.doc ? host.session_id || "" : "";
}

function visibleGraph() {
  const host = graphHost();
  const graph = host?.graph || { nodes: [], edges: [] };
  if (host === state.ide?.engineHost) {
    const nodes = graph.nodes.filter((node) => !DETAIL_KINDS.has(node.kind)).slice(0, 160);
    const ids = new Set(nodes.map((node) => node.id));
    return { nodes, edges: graph.edges.filter((edge) => ids.has(edge.src) && ids.has(edge.dst) && edge.rel !== "has_window") };
  }
  if (host !== state.ctx) return { nodes: graph.nodes, edges: graph.edges };
  // Once a model has named the work, keyphrase topics it didn't use are clutter.
  const tuned = graph.nodes.some((node) => node.props?.actions?.length);
  const isMain = (node) => !DETAIL_KINDS.has(node.kind) && !(tuned && node.kind === "topic" && !node.props?.actions?.length && node.props?.source !== "llm");
  const nodes = graph.nodes.filter((node) => (host.graphKind === "*" ? true : host.graphKind ? node.kind === host.graphKind || node.id === host.node?.node?.id : isMain(node)));
  const ids = new Set(nodes.map((node) => node.id));
  return { nodes, edges: graph.edges.filter((edge) => ids.has(edge.src) && ids.has(edge.dst) && edge.rel !== "has_window") };
}

function graphCanvas() {
  if (!visibleGraph().nodes.length) return '<p class="muted pad">No nodes yet.</p>';
  return '<canvas id="graph3d" class="graph3d" role="img" aria-label="Knowledge graph. Drag to rotate, select a node to open it."></canvas>';
}

function seedSim(nodes, edges) {
  const before = new Map(sim.points.map((point) => [point.node.id, point]));
  const golden = Math.PI * (3 - Math.sqrt(5));
  sim.points = nodes.map((node, i) => {
    const known = before.get(node.id);
    const y = 1 - ((i + 0.5) / nodes.length) * 2;
    const ring = Math.sqrt(1 - y * y);
    const angle = i * golden;
    return {
      node,
      x: known?.x ?? Math.cos(angle) * ring * 190,
      y: known?.y ?? y * 190,
      z: known?.z ?? Math.sin(angle) * ring * 190,
      vx: 0, vy: 0, vz: 0,
    };
  });
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  sim.links = edges.map((edge) => [index.get(edge.src), index.get(edge.dst), edge.weight]);
  sim.maxWeight = Math.max(1, ...nodes.map((node) => node.seconds || node.weight || 1));
  // The layout settles before it's shown, so nothing jumps or bounces on screen.
  sim.alpha = 1;
  while (sim.alpha > 0.02) stepSim();
}

function stepSim() {
  const points = sim.points;
  for (let a = 0; a < points.length; a += 1) {
    const p = points[a];
    for (let b = a + 1; b < points.length; b += 1) {
      const q = points[b];
      const dx = p.x - q.x;
      const dy = p.y - q.y;
      const dz = p.z - q.z;
      const squared = Math.max(4, dx * dx + dy * dy + dz * dz);
      const push = 5200 / (squared * Math.sqrt(squared));
      p.vx += dx * push; p.vy += dy * push; p.vz += dz * push;
      q.vx -= dx * push; q.vy -= dy * push; q.vz -= dz * push;
    }
  }
  for (const [a, b, weight] of sim.links) {
    const p = points[a];
    const q = points[b];
    const dx = q.x - p.x;
    const dy = q.y - p.y;
    const dz = q.z - p.z;
    const dist = Math.max(1, Math.hypot(dx, dy, dz));
    const pull = ((dist - 96) / dist) * 0.01 * Math.min(1.6, 0.4 + Math.log1p(weight) * 0.2);
    p.vx += dx * pull; p.vy += dy * pull; p.vz += dz * pull;
    q.vx -= dx * pull; q.vy -= dy * pull; q.vz -= dz * pull;
  }
  for (const p of points) {
    p.vx = (p.vx - p.x * 0.007) * 0.72;
    p.vy = (p.vy - p.y * 0.009) * 0.72;
    p.vz = (p.vz - p.z * 0.007) * 0.72;
    p.x += p.vx * sim.alpha;
    p.y += p.vy * sim.alpha;
    p.z += p.vz * sim.alpha;
  }
  sim.alpha *= 0.992;
}

const sprites = new Map();

// A shaded ball lit from the upper left, drawn once per shade and stamped at each particle's size.
function sphereSprite(fill) {
  if (sprites.has(fill)) return sprites.get(fill);
  const level = parseInt(fill.slice(1, 3), 16);
  const base = Math.max(level, 58);
  const shade = (value) => {
    const part = Math.round(clamp(value, 0, 255));
    return `rgb(${part},${part},${part})`;
  };
  const size = 96;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const g = canvas.getContext("2d");
  const light = g.createRadialGradient(size * 0.36, size * 0.32, size * 0.03, size / 2, size / 2, size * 0.5);
  light.addColorStop(0, shade(base + (255 - base) * 0.8));
  light.addColorStop(0.42, shade(base));
  light.addColorStop(0.86, shade(base * 0.38));
  light.addColorStop(1, shade(base * 0.2));
  g.fillStyle = light;
  g.beginPath();
  g.arc(size / 2, size / 2, size / 2, 0, TAU);
  g.fill();
  sprites.set(fill, canvas);
  return canvas;
}

function drawFloor(g, project, pitch, scale) {
  const seen = clamp((pitch - 0.02) / 0.18);
  const middle = project(0, FLOOR_Y, 0);
  if (!seen || !middle) return;
  const radius = 520 * middle.near * scale;
  g.save();
  g.translate(middle.x, middle.y);
  g.scale(1, Math.max(0.12, Math.sin(pitch)));
  const pool = g.createRadialGradient(0, 0, 0, 0, 0, radius);
  pool.addColorStop(0, `rgba(255,255,255,${0.09 * seen})`);
  pool.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = pool;
  g.beginPath();
  g.arc(0, 0, radius, 0, TAU);
  g.fill();
  g.restore();
}

function drawSim() {
  const canvas = sim.canvas;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * dpr) || canvas.height !== Math.round(rect.height * dpr)) {
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
  }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, rect.width, rect.height);
  const { pitch } = sim;
  const cy = Math.cos(sim.yaw);
  const sy = Math.sin(sim.yaw);
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  const scale = (sim.zoom * Math.min(rect.width, rect.height)) / 740;
  const project = (x, y, z) => {
    const across = x * cy - z * sy;
    const depth = x * sy + z * cy;
    const up = y * cp - depth * sp;
    const away = y * sp + depth * cp;
    if (CAMERA + away < 60) return null;
    const near = CAMERA / (CAMERA + away);
    return { x: rect.width / 2 + across * near * scale, y: rect.height * 0.5 + up * near * scale, z: away, near };
  };

  drawFloor(g, project, pitch, scale);

  const shown = sim.points.map((point, index) => {
    const spot = project(point.x, point.y, point.z) || { x: -999, y: -999, z: 0, near: 0.01 };
    const size = 1.8 + Math.sqrt((point.node.seconds || point.node.weight || 1) / sim.maxWeight) * 9;
    return {
      index, node: point.node, z: spot.z, near: spot.near, world: point,
      sx: spot.x, sy: spot.y,
      r: Math.max(0.8, size * spot.near * Math.min(1.5, scale + 0.35)),
      fade: 1 - clamp((spot.z + 260) / 560) * 0.8,
    };
  });
  const host = graphHost();
  const selected = host?.node?.node?.id;
  const neighbors = new Set((host?.node?.neighbors || []).map((item) => item.id));
  const lit = (node) => !selected || node.id === selected || neighbors.has(node.id);

  // Shadows on the floor tell you how high each particle is.
  const flat = Math.max(0.14, Math.sin(pitch));
  if (pitch > 0.02) {
    for (const item of shown) {
      const spot = project(item.world.x, FLOOR_Y, item.world.z);
      if (!spot) continue;
      const lift = clamp((FLOOR_Y - item.world.y) / 480);
      const radius = (item.r / item.near) * spot.near * (1.1 + lift * 0.9);
      g.globalAlpha = (0.72 - lift * 0.42) * clamp((pitch - 0.02) / 0.18) * (lit(item.node) ? 1 : 0.4);
      g.fillStyle = "#000";
      g.beginPath();
      g.ellipse(spot.x, spot.y, radius, radius * flat, 0, 0, TAU);
      g.fill();
      if (item.node.id === selected || item.node.id === sim.hover) {
        g.globalAlpha = 0.5;
        g.strokeStyle = "#fff";
        g.lineWidth = 0.7;
        g.setLineDash([2, 4]);
        g.beginPath();
        g.moveTo(item.sx, item.sy);
        g.lineTo(spot.x, spot.y);
        g.stroke();
        g.setLineDash([]);
      }
    }
    g.globalAlpha = 1;
  }

  // Lines and particles are painted back to front together, so near ones cover far ones.
  const painted = [
    ...shown.map((item) => ({ z: item.z, item })),
    ...sim.links.map(([a, b]) => ({ z: (shown[a].z + shown[b].z) / 2 + 2, p: shown[a], q: shown[b] })),
  ].sort((a, b) => b.z - a.z);
  for (const entry of painted) {
    if (entry.p) {
      const { p, q } = entry;
      const active = selected && (p.node.id === selected || q.node.id === selected);
      const depth = (p.fade + q.fade) / 2;
      g.globalAlpha = 1;
      g.strokeStyle = `rgba(255,255,255,${active ? 0.85 : (selected ? 0.07 : 0.24) * depth})`;
      g.lineWidth = (active ? 1.2 : 0.5) + ((p.near + q.near) / 2) * 0.55;
      g.beginPath();
      g.moveTo(p.sx, p.sy);
      g.lineTo(q.sx, q.sy);
      g.stroke();
      continue;
    }
    const { item } = entry;
    const style = KIND_STYLE[item.node.kind] || { fill: "#808080" };
    const on = item.node.id === selected || item.node.id === sim.hover;
    g.globalAlpha = lit(item.node) ? item.fade : item.fade * 0.32;
    const size = item.r * 2;
    g.drawImage(sphereSprite(style.fill), item.sx - item.r, item.sy - item.r, size, size);
    if (style.stroke || style.ring || on) {
      g.beginPath();
      g.arc(item.sx, item.sy, item.r + (style.ring ? 2.2 * item.near : 0), 0, TAU);
      g.lineWidth = on ? 2 : 0.8;
      g.strokeStyle = on ? "#fff" : "rgba(255,255,255,0.55)";
      g.stroke();
    }
  }
  const ranked = [...shown].sort((a, b) => (b.node.seconds || b.node.weight) - (a.node.seconds || a.node.weight)).slice(0, 14);
  const named = new Set(ranked.map((item) => item.node.id));
  const family = getComputedStyle(document.body).fontFamily;
  g.textBaseline = "middle";
  g.lineJoin = "round";
  for (const item of [...shown].sort((a, b) => b.z - a.z)) {
    const id = item.node.id;
    const on = id === selected || id === sim.hover;
    if (!on && !neighbors.has(id) && !(named.has(id) && !selected)) continue;
    const label = snippet(item.node.label, 30);
    // Type shrinks and dims with distance, like the particles it names.
    g.font = `${(9.5 + item.near * 2.4).toFixed(1)}px ${family}`;
    g.globalAlpha = on ? 1 : clamp(item.fade + 0.1);
    g.lineWidth = 3;
    g.strokeStyle = "#000";
    g.strokeText(label, item.sx + item.r + 5, item.sy);
    g.fillStyle = on ? "#fff" : "#c4c4c4";
    g.fillText(label, item.sx + item.r + 5, item.sy);
  }
  g.globalAlpha = 1;
  sim.screen = shown;
}

function loopSim(now) {
  if (!sim.canvas?.isConnected) {
    sim.raf = 0;
    return;
  }
  if (!sim.drag) {
    sim.yaw += sim.spin;
    sim.spin *= 0.85;
    if (now - sim.touched > 3000 && !sim.hover && !reducedMotion) sim.yaw += 0.0012;
  }
  drawSim();
  sim.raf = requestAnimationFrame(loopSim);
}

function pickSim(event) {
  const rect = sim.canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  let best = null;
  let bestDistance = Infinity;
  for (const item of sim.screen) {
    const distance = Math.hypot(item.sx - x, item.sy - y);
    if (distance < item.r + 6 && (distance < bestDistance || item.z < best.z)) {
      best = item;
      bestDistance = distance;
    }
  }
  return best?.node || null;
}

function mountGraph() {
  const canvas = $("#graph3d");
  if (!canvas) {
    sim.canvas = null;
    return;
  }
  const { nodes, edges } = visibleGraph();
  const key = `${state.view}:${state.ctx?.id}:${state.doc?.id}:${state.flow?.id}:${state.ide?.id}:${state.ide?.active}:${nodes.map((node) => node.id).join("|")}:${edges.length}`;
  if (key !== sim.key) {
    seedSim(nodes, edges);
    sim.key = key;
  }
  sim.canvas = canvas;
  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    sim.drag = { x: event.clientX, y: event.clientY, moved: false };
    sim.spin = 0;
  });
  canvas.addEventListener("pointermove", (event) => {
    if (sim.drag) {
      const dx = event.clientX - sim.drag.x;
      const dy = event.clientY - sim.drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) sim.drag.moved = true;
      sim.spin = dx * 0.006;
      sim.yaw += sim.spin;
      sim.pitch = clamp(sim.pitch + dy * 0.004, -0.25, 1.25);
      sim.drag.x = event.clientX;
      sim.drag.y = event.clientY;
      sim.touched = performance.now();
      return;
    }
    const node = pickSim(event);
    sim.hover = node?.id || null;
    canvas.style.cursor = node ? "pointer" : "grab";
  });
  canvas.addEventListener("pointerleave", () => {
    sim.hover = null;
  });
  canvas.addEventListener("pointerup", async (event) => {
    const clicked = sim.drag && !sim.drag.moved ? pickSim(event) : null;
    sim.drag = null;
    sim.touched = performance.now();
    if (!clicked) return;
    try {
      await actions.node({ dataset: { id: clicked.id } });
    } catch (error) {
      toast(error.message || "Request failed");
    }
  });
  // Pinch arrives as a ctrl wheel; a plain wheel keeps scrolling the page.
  canvas.addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    sim.zoom = Math.max(0.4, Math.min(4, sim.zoom * Math.exp(-event.deltaY * 0.01)));
  }, { passive: false });
  if (!sim.raf) sim.raf = requestAnimationFrame(loopSim);
}

function nodePanel() {
  const data = graphHost()?.node;
  if (!data) {
    return "";
  }
  const node = data.node;
  const actionRows = (data.actions || [])
    .map((item) => `<li>${item.status ? `<span class="tag">${esc(item.status.replace("_", " "))}</span> ` : ""}${esc(item.text)}${item.at ? ` <span class="faint">${esc(stamp(item.at))}</span>` : ""}${(item.frames || []).slice(0, 1).map((frame) => ` <button class="link" type="button" data-act="frame" data-id="${frame}">View</button>`).join("")}</li>`)
    .join("");
  const traits = (node.props?.traits || []).map((trait) => `<span class="tag">${esc(trait)}</span>`).join("");
  const eventRows = (data.events || [])
    .map((event) => `<div class="line"><span class="kind">${esc(event.event_type)}</span> ${esc(clock(event.timestamp))} · ${esc(snippet(event.text, 120))}</div>`)
    .join("");
  const frameRows = (data.frames || [])
    .map((frame) => `<button class="list-row" type="button" data-act="frame" data-id="${frame.id}"><span class="kind">${esc(clock(frame.timestamp))}</span><span>${esc(frame.app_name)} · ${esc(snippet(frame.window_name, 60))}</span><span class="num faint">${frame.id}</span></button>`)
    .join("");
  const rows = (data.neighbors || [])
    .map((item) => `<button class="list-row" type="button" data-act="node" data-id="${esc(item.id)}"><span class="kind">${kindSwatch(item.kind)}${esc(item.kind)}</span><span>${esc(item.direction === "out" ? "→" : "←")} <em>${esc(item.rel)}</em> <strong>${esc(item.label)}</strong></span><span class="num faint">${Math.round(item.weight)}</span></button>`)
    .join("");
  const fold = (title, count, body) => (count ? `<details class="fold"><summary>${esc(title)} · ${count}</summary>${body}</details>` : "");
  return section(
    node.label,
    `<p class="label">${kindSwatch(node.kind)}${esc(node.kind)}</p>
    ${traits ? `<div class="tags">${traits}</div>` : ""}
    ${actionRows ? `<ul class="notes">${actionRows}</ul>` : ""}
    ${fold("System events", (data.events || []).length, eventRows)}
    ${fold("Frames", (data.frames || []).length, `<div class="list">${frameRows}</div>`)}
    ${fold("Connections", (data.neighbors || []).length, `<div class="list">${rows}</div>`)}
    <details class="fold"><summary>Details</summary><dl class="props"><dt>ID</dt><dd><code>${esc(node.id)}</code></dd><dt>Seen</dt><dd>${Math.round(node.weight)}×</dd><dt>First</dt><dd>${esc(stamp(node.first_seen))}</dd><dt>Last</dt><dd>${esc(stamp(node.last_seen))}</dd></dl></details>
    ${graphHost() === state.ctx ? `<div class="actions"><button class="btn sm" type="button" data-act="node-context" data-label="${esc(node.label)}">Write a context file about this</button></div>` : ""}`,
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
      ${state.llm?.enabled ? '<button class="btn" type="button" data-act="graph-tune">Refine with model</button>' : '<button class="btn" type="button" data-act="nav" data-view="settings">Set up a model</button>'}
    </form>
    <div class="tags">${kinds}<button class="tag click" type="button" data-act="graph-kind" data-kind="${ctx.graphKind ? "" : "*"}">${ctx.graphKind ? "Main nodes" : "Show everything"}</button></div>
    ${results}
    <div class="graph-grid">
      <section class="panel-box graph-box">${graphCanvas()}</section>
      <div class="stack">${nodePanel()}${transitions ? section("Habitual transitions", transitions) : ""}</div>
    </div>
    ${chatHtml()}
  </div>`;
}

/* Chat beneath a recording's graph */

function chatKey() {
  return state.ctx?.kind === "session" ? String(state.ctx.id) : "all";
}

function chatTurns() {
  const key = chatKey();
  if (!state.chats[key]) state.chats[key] = [];
  return state.chats[key];
}

function threadHtml() {
  const turns = chatTurns();
  if (!turns.length) {
    return `<div class="tags">${PROMPTS.map((prompt) => `<button class="tag click" type="button" data-act="chat-prompt" data-q="${esc(prompt)}">${esc(prompt)}</button>`).join("")}</div>`;
  }
  return turns.map((turn) => {
    if (turn.role === "user") return `<div class="chat-q">${esc(turn.text)}</div>`;
    const cites = (turn.citations || []).map((cite) => `<button class="tag click" type="button" data-act="citation" data-type="${esc(cite.type || "")}" data-id="${esc(cite.id ?? "")}" data-frame="${esc(cite.frame_id ?? "")}">${esc(cite.label || "Source")}</button>`).join("");
    const body = turn.pending ? '<div class="typing"><i></i><i></i><i></i></div>' : mdHtml(turn.text);
    return `<div class="chat-a prose">${body}${cites ? `<div class="tags">${cites}</div>` : ""}</div>`;
  }).join("");
}

function chatHtml() {
  const scope = state.ctx?.kind === "session" ? "this recording" : "all your history";
  const clear = chatTurns().length ? '<button class="btn sm" type="button" data-act="chat-clear">Clear</button>' : "";
  return section(
    `Ask about ${scope}`,
    `<div id="chat-thread" class="chat-thread">${threadHtml()}</div>
    <form class="inline" data-form="chat"><input id="chat-input" name="question" placeholder="Ask what happened, what's unfinished, or where something was" autocomplete="off" /><button class="btn primary" type="submit">Ask</button></form>`,
    clear,
  );
}

function syncThread() {
  const thread = $("#chat-thread");
  if (!thread) return;
  thread.innerHTML = threadHtml();
  thread.scrollTop = thread.scrollHeight;
}

async function sendChat(question) {
  const cleaned = question.trim();
  if (!cleaned) return;
  const turns = chatTurns();
  const history = turns.map((turn) => ({ role: turn.role, text: turn.text }));
  turns.push({ role: "user", text: cleaned });
  const reply = { role: "assistant", text: "", pending: true, citations: [] };
  turns.push(reply);
  syncThread();
  const input = $("#chat-input");
  if (input) input.value = "";
  const headers = { "Content-Type": "application/json" };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  try {
    const response = await fetch("/ask/stream", { method: "POST", headers, body: JSON.stringify({ question: cleaned, session_id: ctxScope().session_id || null, history }) });
    if (!response.ok || !response.body) {
      const error = await response.json().catch(() => ({ error: "Ask failed" }));
      throw new Error(error.error || "Ask failed");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
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
        if (payload.delta) reply.text += payload.delta;
        if (payload.done) {
          reply.citations = payload.citations || [];
          if (payload.answer) reply.text = payload.answer;
        }
      }
      reply.pending = false;
      syncThread();
    }
  } catch (error) {
    reply.text = error.message || "Ask failed";
  }
  reply.pending = false;
  syncThread();
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
    ${section("Frames", `<div class="film">${film || '<p class="muted">No frames.</p>'}</div>`)}
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
  let results = "";
  if (found) {
    const nodes = found.nodes.map((item) => `<button class="list-row" type="button" data-act="search-node" data-id="${esc(item.id)}"><span class="kind">${kindSwatch(item.kind)}${esc(item.kind)}</span><span><strong>${esc(item.label)}</strong></span><span class="num faint">${mins(item.seconds)}</span></button>`).join("");
    const content = found.content.map((item, index) => {
      const body = item.content || {};
      const title = body.app_name || body.speaker_name || item.type;
      const detail = body.window_name || body.event_type || "";
      return `<button class="list-row" type="button" data-act="result" data-index="${index}"><span class="kind">${esc(item.type)}</span><span><strong>${esc(title)}</strong> ${esc(detail)}<br><span class="snippet">${esc(snippet(body.text, 220))}</span></span><span class="num faint">${esc(clock(body.timestamp))}</span></button>`;
    }).join("");
    const urls = found.urls.map((item) => `<button class="list-row" type="button" data-act="frame" data-id="${item.first_frame}"><span class="kind">url</span><span><strong>${esc(snippet(item.title, 80))}</strong><br><span class="snippet">${esc(item.url)}</span></span><span class="num faint">${item.frames}</span></button>`).join("");
    const files = found.contexts.map((item) => `<button class="list-row" type="button" data-act="open-doc" data-id="${item.id}"><span class="kind">context</span><span><strong>${esc(item.title)}</strong><br><span class="snippet">${esc(item.snippet)}</span></span><span class="num faint">${esc(stamp(item.created_at))}</span></button>`).join("");
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

function slots(text) {
  return inlineMd(text).replace(/\{([\w -]+)\}/g, '<span class="slot">{$1}</span>');
}

function workflowsHtml() {
  if (state.flow) return flowHtml();
  const items = state.workflows?.data || [];
  return `<div class="stack">
    ${pageHead("Workflows", "", '<button class="btn primary" type="button" data-act="workflow-new">Generate with model</button>')}
    ${items.length ? `<div class="flow-grid">${items.map(flowCard).join("")}</div>` : `<section class="panel-box hero"><h2>No workflows yet</h2></section>`}
  </div>`;
}

function flowHtml() {
  const flow = state.flow;
  const spec = flow.spec;
  const inputs = spec.inputs.map((entry) => `<div class="flow-input"><span class="slot">${esc(entry.name)}</span><span>${slots(entry.description)}</span></div>`).join("");
  const steps = spec.steps.map((step, index) => {
    const checks = (step.checks || []).map((check) => `<li>${slots(check)}</li>`).join("");
    return `<li class="flow-step">
      <span class="flow-num">${index + 1}</span>
      <div class="flow-step-body">
        <h3>${slots(step.title)}</h3>
        <div class="tags">${step.app ? `<span class="tag">${esc(step.app)}</span>` : ""}${step.confirm ? '<span class="tag confirm">Confirm first</span>' : ""}</div>
        <p>${slots(step.detail)}</p>
        ${checks ? `<ul>${checks}</ul>` : ""}
      </div>
    </li>`;
  }).join("");
  const sessionLinks = flow.sources.session_ids.map((id) => `<button class="tag click" type="button" data-act="open-recording" data-id="${id}">${esc(recordingLabel(id))}</button>`).join("");
  const contextLinks = flow.sources.context_ids.map((id) => `<button class="tag click" type="button" data-act="open-doc" data-id="${id}">${esc((state.saved || []).find((item) => item.id === id)?.title || `Context ${id}`)}</button>`).join("");
  return `<div class="flow">
    <header class="flow-head">
      <h1>${esc(flow.title)}</h1>
      <p class="flow-lead">${slots(spec.summary)}</p>
      <div class="actions"><a class="btn primary" href="${media(`/workflows/${flow.id}/document`)}" download>Download .md</a><button class="btn" type="button" data-act="copy-text" data-text="get_workflow(id=${flow.id})">Copy MCP call</button><button class="btn" type="button" data-act="flow-delete" data-id="${flow.id}">Delete</button></div>
    </header>
    <div class="flow-pair">
      <section class="flow-callout"><p class="label">When to run it</p><p>${slots(spec.trigger)}</p></section>
      <section class="flow-callout"><p class="label">What you end up with</p><p>${slots(spec.outcome)}</p></section>
    </div>
    <div class="doc-grid">
      <div class="stack">
        ${inputs ? `<section class="flow-part"><h2>What it needs</h2><div class="flow-inputs">${inputs}</div></section>` : ""}
        <section class="flow-part"><h2>Steps</h2><ol class="flow-steps">${steps}</ol></section>
        ${spec.guardrails.length ? `<section class="flow-part flow-guard"><h2>Ask before doing</h2><ul>${spec.guardrails.map((entry) => `<li>${slots(entry)}</li>`).join("")}</ul></section>` : ""}
      </div>
      <aside class="stack">
        ${graphSection("Context graph", "workflow", flow.id, flow.graph)}
        ${flow.node ? nodePanel() : ""}
        ${agentSection("get_workflow", flow.id)}
        ${section("Made from", `<div class="tags">${sessionLinks}${contextLinks}</div><details class="fold"><summary>What it saw</summary><p class="snippet">${esc(flow.evidence)}</p><div class="tags">${spec.frames.slice(0, 8).map((frame) => `<button class="tag click" type="button" data-act="frame" data-id="${esc(frame)}">Frame ${esc(frame)}</button>`).join("")}</div></details>`)}
      </aside>
    </div>
  </div>`;
}

function workflowDialog(session, context) {
  const sessions = state.sessions?.data || [];
  const saved = state.saved || [];
  const noPick = !session && !context;
  const sessionRows = sessions.filter((item) => item.frames > 0).map((item) => `<label class="pick"><input type="checkbox" name="session" value="${item.id}" ${noPick || String(item.id) === session ? "checked" : ""} /><span><strong>${esc(item.label || `Recording ${item.id}`)}</strong><span class="faint">${esc(stamp(item.started_at))}</span></span></label>`).join("");
  const contextRows = saved.map((item) => `<label class="pick"><input type="checkbox" name="context" value="${item.id}" ${String(item.id) === context ? "checked" : ""} /><span><strong>${esc(item.title)}</strong><span class="faint">${esc(recordingLabel(item.session_id))}</span></span></label>`).join("");
  const noModel = state.llm && !state.llm.enabled;
  return `<div class="backdrop center" data-act="close"><form class="modal" data-sheet data-form="workflow-new">
    <header><h2>New workflow</h2><button class="btn sm" type="button" data-act="close">Close</button></header>
    ${noModel ? '<p class="banner">Workflows are written by a model, so add one in Settings first.</p>' : ""}
    <label class="field">Name<input name="name" placeholder="Optional" autocomplete="off" /></label>
    <label class="field">What it's for<textarea name="purpose" rows="3" placeholder="Optional"></textarea></label>
    <fieldset class="picks"><legend><span class="label">Recordings to scan</span><button class="link" type="button" data-act="pick-all" data-name="session">All</button><button class="link" type="button" data-act="pick-none" data-name="session">None</button></legend><div class="pick-list">${sessionRows || '<p class="faint">No recordings yet.</p>'}</div></fieldset>
    <fieldset class="picks"><legend><span class="label">Context files to scan</span><button class="link" type="button" data-act="pick-all" data-name="context">All</button><button class="link" type="button" data-act="pick-none" data-name="context">None</button></legend><div class="pick-list">${contextRows || '<p class="faint">No context files yet.</p>'}</div></fieldset>
    <p class="wiz-note" id="workflow-note"></p>
    <div class="actions"><button class="btn primary" type="submit" ${noModel ? "disabled" : ""}>Generate</button></div>
  </form></div>`;
}

/* Harnesses */

function harnessesHtml() {
  if (state.wizard) return wizardHtml();
  if (state.ide) return ideHtml();
  const cards = (state.harnesses || []).map((item) => {
    const total = Math.max(1, item.contexts + item.workflows);
    return `<article class="harness-card click" data-act="harness-open" data-id="${item.id}">
      <header><h3>${esc(item.name)}</h3></header>
      ${item.purpose ? `<p class="muted clamp">${esc(item.purpose)}</p>` : ""}
      <div class="compo"><i class="c-ctx" style="flex:${item.contexts || 0}"></i><i class="c-flow" style="flex:${item.workflows || 0}"></i>${total ? "" : ""}</div>
      <p class="legend"><span class="t-ctx">${item.contexts} context${item.contexts === 1 ? "" : "s"}</span><span class="t-flow">${item.workflows} workflow${item.workflows === 1 ? "" : "s"}</span><span class="t-graph">${item.session_ids.length} recording${item.session_ids.length === 1 ? "" : "s"}</span></p>
    </article>`;
  }).join("");
  return `<div class="stack">
    ${pageHead("Harnesses", "", '<button class="btn primary" type="button" data-act="harness-new">New harness</button>')}
    <div class="harness-grid">
      <button class="harness-card new" type="button" data-act="harness-new"><span class="plus">+</span><strong>New harness</strong></button>
      ${cards}
    </div>
  </div>`;
}

/* New harness: a design process in steps */

const WIZARD_STEPS = ["Purpose", "Recordings", "Contexts", "Workflows", "Review"];

function newWizard(session = null) {
  return { step: 0, name: "", purpose: "", session_ids: session ? [session] : [], context_ids: [], workflow_ids: [], new_workflows: [], suggested: null, suggestKey: "", note: "", busy: false };
}

async function loadWizard() {
  const [sessions, saved, flows] = await Promise.all([api("/sessions?limit=200"), api("/contexts?limit=500"), api("/workflows")]);
  state.sessions = sessions;
  state.saved = saved.data;
  state.workflows = flows;
}

function pickRow(field, item, title, meta) {
  const wiz = state.wizard;
  const on = wiz[field].includes(item.id);
  const picked = field === "context_ids" ? wiz.suggested?.context_ids : field === "workflow_ids" ? wiz.suggested?.workflow_ids : null;
  return `<label class="pick ${on ? "is-on" : ""}"><input type="checkbox" data-wiz-pick="${field}" value="${item.id}" ${on ? "checked" : ""} /><span><strong>${esc(title)}</strong><span class="faint">${esc(meta)}</span></span>${wiz.suggested?.model && picked?.includes(item.id) ? '<span class="tag fit">Suggested</span>' : ""}</label>`;
}

function wizardStepHtml() {
  const wiz = state.wizard;
  const sessions = (state.sessions?.data || []).filter((item) => item.frames > 0);
  if (wiz.step === 0) {
    return `<h2>What is this harness for?</h2>
      
      <label class="field">Name<input data-wiz="name" value="${esc(wiz.name)}" placeholder="Recruiting" autocomplete="off" /></label>
      <div class="tags">${HARNESS_EXAMPLES.map((name) => `<button class="tag click" type="button" data-act="wiz-example" data-name="${esc(name)}">${esc(name)}</button>`).join("")}</div>
      <label class="field">Describe it in detail<textarea data-wiz="purpose" rows="7" placeholder="What should an agent be able to do, what does good look like, and what should it never do on its own?">${esc(wiz.purpose)}</textarea></label>`;
  }
  if (wiz.step === 1) {
    return `<h2>Which recordings should its graph know?</h2>
      
      <div class="pick-list tall">${sessions.map((item) => pickRow("session_ids", item, item.label || `Recording ${item.id}`, stamp(item.started_at))).join("") || '<p class="faint">No recordings yet. You can still build from context files and workflows.</p>'}</div>`;
  }
  if (wiz.step === 2) {
    const chosen = wiz.session_ids;
    const needs = chosen.filter((id) => !(state.saved || []).some((item) => item.session_id === id));
    const files = [...(state.saved || [])].sort((a, b) => Number(chosen.includes(b.session_id)) - Number(chosen.includes(a.session_id)));
    return `<h2>Which contexts belong in it?</h2>
      
      ${needs.length ? `<div class="inline spread callout"><span>${needs.length} of your recordings ${needs.length === 1 ? "has" : "have"} no contexts or workflows yet.</span><button class="btn sm" type="button" data-act="wiz-generate">${wiz.busy ? "Writing…" : "Write them now"}</button></div>` : ""}
      <div class="pick-list tall">${files.map((item) => pickRow("context_ids", item, item.title, recordingLabel(item.session_id))).join("") || '<p class="faint">No context files yet.</p>'}</div>`;
  }
  if (wiz.step === 3) {
    const flows = state.workflows?.data || [];
    const rows = wiz.new_workflows.map((item, index) => `<div class="new-flow"><input data-wiz-flow="${index}" data-key="name" value="${esc(item.name)}" placeholder="Customize resume bullet points to keep their length" autocomplete="off" /><input data-wiz-flow="${index}" data-key="purpose" value="${esc(item.purpose)}" placeholder="What it's for" autocomplete="off" /><button class="btn sm" type="button" data-act="wiz-flow-remove" data-index="${index}">Remove</button></div>`).join("");
    return `<h2>Which workflows does it need?</h2>
      
      <div class="pick-list">${flows.map((item) => pickRow("workflow_ids", item, item.title, "")).join("") || '<p class="faint">No workflows yet.</p>'}</div>
      <div class="inline spread"><h3>Workflows to write</h3><button class="btn sm" type="button" data-act="wiz-flow-add">Add one</button></div>
      
      ${rows || ""}
      ${state.llm?.enabled ? "" : '<p class="banner">Writing new workflows needs a model, which you can add in Settings.</p>'}`;
  }
  const files = (state.saved || []).filter((item) => wiz.context_ids.includes(item.id));
  const flows = (state.workflows?.data || []).filter((item) => wiz.workflow_ids.includes(item.id));
  const adding = wiz.new_workflows.filter((item) => item.name.trim());
  const line = (name, css, note = "") => `<div class="tree-line ${css}"><span>${esc(name)}</span>${note ? `<em>${esc(note)}</em>` : ""}</div>`;
  return `<h2>${esc(wiz.name || "Untitled harness")}</h2>
    <p class="muted">${esc(wiz.purpose)}</p>
    <div class="tree-preview">
      ${line("AGENTS.md", "t-agents", "")}
      ${files.map((item) => line(`contexts/${item.title}`, "t-ctx")).join("")}
      ${flows.map((item) => line(`workflows/${item.title}`, "t-flow")).join("")}
      ${adding.map((item) => line(`workflows/${item.name}`, "t-flow", "to write")).join("")}
      ${line(`graph/ from ${wiz.session_ids.length} recording${wiz.session_ids.length === 1 ? "" : "s"}`, "t-graph")}
    </div>
    `;
}

function wizardHtml() {
  const wiz = state.wizard;
  const steps = WIZARD_STEPS.map((title, index) => `<button class="wiz-step ${index === wiz.step ? "is-on" : ""} ${index < wiz.step ? "is-done" : ""}" type="button" data-act="wiz-go" data-step="${index}"><span class="wiz-num">${index < wiz.step ? "✓" : index + 1}</span><strong>${title}</strong></button>`).join("");
  const last = wiz.step === WIZARD_STEPS.length - 1;
  return `<div class="wizard">
    <aside class="wiz-steps"><p class="nav-label">New harness</p>${steps}</aside>
    <section class="wiz-body">
      ${wizardStepHtml()}
      ${wiz.note ? `<p class="wiz-note">${esc(wiz.note)}</p>` : ""}
      <footer class="actions spread-row">
        <button class="btn" type="button" data-act="${wiz.step ? "wiz-back" : "nav"}" data-view="harnesses" ${wiz.busy ? "disabled" : ""}>${wiz.step ? "Back" : "Cancel"}</button>
        <button class="btn primary" type="button" data-act="${last ? "wiz-build" : "wiz-next"}" ${wiz.busy ? "disabled" : ""}>${wiz.busy && last ? "Building…" : last ? "Build harness" : "Continue"}</button>
      </footer>
    </section>
  </div>`;
}

async function wizardSuggest() {
  const wiz = state.wizard;
  const key = JSON.stringify([wiz.name, wiz.purpose, wiz.session_ids]);
  if (key === wiz.suggestKey) return;
  wiz.suggestKey = key;
  wiz.busy = true;
  wiz.note = state.llm?.enabled ? "Reading your recordings and picking what fits…" : "";
  paintView();
  try {
    const found = await api("/harnesses/suggest", { method: "POST", body: JSON.stringify({ name: wiz.name, purpose: wiz.purpose, session_ids: wiz.session_ids }) });
    wiz.suggested = found;
    wiz.context_ids = found.context_ids;
    wiz.workflow_ids = found.workflow_ids;
    const typed = wiz.new_workflows.filter((item) => item.name.trim());
    const names = new Set(typed.map((item) => item.name.toLowerCase()));
    wiz.new_workflows = [...typed, ...found.missing_workflows.filter((item) => !names.has(item.name.toLowerCase()))];
    wiz.note = found.error ? `The model couldn't help here: ${found.error}` : "";
  } finally {
    wiz.busy = false;
  }
}

/* The harness IDE: files on the left, the open file in the middle, chat on the right */

const ENGINE_PATH = "graph/engine";

function newIde(id) {
  return { id, data: null, open: [], active: null, mode: "preview", drafts: {}, hosts: {}, engineHost: { graph: null, node: null }, engineFind: null, adding: null, busy: false, pending: null, exportOpen: false };
}

async function loadIde() {
  const ide = state.ide;
  ide.data = await api(`/harnesses/${ide.id}`);
  if (!ide.active) {
    ide.active = "AGENTS.md";
    ide.open = ["AGENTS.md"];
  }
  await loadSessions(200);
}

function ideFile(path) {
  return state.ide.data?.files.find((item) => item.path === path) || null;
}

function ideGraphHost() {
  const ide = state.ide;
  if (ide.active === ENGINE_PATH) return ide.engineHost.graph ? ide.engineHost : null;
  const file = ideFile(ide.active);
  if (!file?.graph || ide.mode !== "preview") return null;
  ide.hosts[file.path] = ide.hosts[file.path] || { graph: file.graph, node: null };
  ide.hosts[file.path].graph = file.graph;
  return ide.hosts[file.path];
}

function ideDirty(path) {
  const draft = state.ide.drafts[path];
  return draft != null && draft !== ideFile(path)?.content;
}

function ideRow(item) {
  const ide = state.ide;
  const name = item.path.includes("/") ? item.path.split("/")[1] : item.path;
  return `<button class="file-row ${ide.active === item.path ? "is-on" : ""}" type="button" data-act="ide-open" data-path="${esc(item.path)}" title="${esc(item.title)}"><i class="glyph g-${item.kind}"></i><span>${esc(name)}</span>${ideDirty(item.path) ? '<b class="dirty"></b>' : ""}</button>`;
}

function ideExplorer() {
  const ide = state.ide;
  const data = ide.data;
  const group = (kind, folder, css) => {
    const files = data.files.filter((item) => item.kind === kind);
    return `<div class="tree-group"><div class="tree-head"><span class="${css}">${folder}/</span><span class="count">${files.length}</span><button class="icon-btn" type="button" data-act="ide-add" data-kind="${kind}" title="New ${kind}">+</button></div>
      ${ide.adding === kind ? `<form class="tree-add" data-form="ide-new-file"><input id="ide-new-title" name="title" placeholder="${kind === "context" ? "Researching job companies" : "Customize resume bullets"}" autocomplete="off" /></form>` : ""}
      ${files.map(ideRow).join("")}</div>`;
  };
  const root = data.files.find((item) => item.kind === "agents");
  return `<aside class="ide-tree">
    <header class="ide-title"><strong>${esc(data.name)}</strong>${data.purpose ? `<p class="faint clamp" title="${esc(data.purpose)}">${esc(data.purpose)}</p>` : ""}</header>
    <div class="tree-scroll">
      ${root ? ideRow(root) : ""}
      ${group("context", "contexts", "t-ctx")}
      ${group("workflow", "workflows", "t-flow")}
      <div class="tree-group"><div class="tree-head"><span class="t-graph">graph/</span></div><button class="file-row ${ide.active === ENGINE_PATH ? "is-on" : ""}" type="button" data-act="ide-open" data-path="${ENGINE_PATH}"><i class="glyph g-graph"></i><span>engine</span></button></div>
    </div>
    <footer class="ide-foot">
      <div class="menu">
        <button class="btn sm" type="button" data-act="ide-export">Export</button>
        ${ide.exportOpen ? `<div class="menu-pop">
          <a href="${media(`/harnesses/${ide.id}/export?format=zip`)}" download>Folder as .zip</a>
          <a href="${media(`/harnesses/${ide.id}/export?format=md`)}" download>One .md file</a>
          <a href="${media(`/harnesses/${ide.id}/export?format=json`)}" download>Graph as JSON</a>
          <button type="button" data-act="copy-text" data-text="get_harness(id=${ide.id})">Copy MCP call</button>
        </div>` : ""}
      </div>
      <button class="btn sm" type="button" data-act="harness-delete" data-id="${ide.id}">Delete</button>
    </footer>
  </aside>`;
}

function ideTabs() {
  const ide = state.ide;
  return ide.open.map((path) => {
    const name = path === ENGINE_PATH ? "engine" : path.split("/").pop();
    return `<div class="ide-tab ${ide.active === path ? "is-on" : ""}"><button type="button" data-act="ide-open" data-path="${esc(path)}">${ideDirty(path) ? '<b class="dirty"></b>' : ""}${esc(name)}</button><button class="tab-x" type="button" data-act="ide-close" data-path="${esc(path)}" aria-label="Close ${esc(name)}">×</button></div>`;
  }).join("");
}

function stripFront(text) {
  return text.replace(/^---\n[\s\S]*?\n---\n+/, "");
}

function ideEditor(file) {
  const ide = state.ide;
  const text = ide.drafts[file.path] ?? file.content;
  const toolbar = `<div class="ide-bar"><code class="ide-path">${esc(file.path)}</code><span class="grow"></span>
    <div class="seg"><button class="${ide.mode === "preview" ? "is-on" : ""}" type="button" data-act="ide-mode" data-mode="preview">Preview</button><button class="${ide.mode === "source" ? "is-on" : ""}" type="button" data-act="ide-mode" data-mode="source">Source</button></div>
    ${ideDirty(file.path) ? '<button class="btn sm primary" type="button" data-act="ide-save">Save <kbd>⌘S</kbd></button>' : ""}
    ${file.kind === "agents" ? "" : `<button class="btn sm" type="button" data-act="ide-delete-file" data-path="${esc(file.path)}">Remove</button>`}
  </div>`;
  if (ide.mode === "source") {
    const lines = text.split("\n").map((_, index) => index + 1).join("\n");
    return `${toolbar}<div class="ide-source"><pre id="ide-gutter" class="gutter" aria-hidden="true">${lines}</pre><textarea id="ide-text" spellcheck="false" aria-label="Edit ${esc(file.path)}">${esc(text)}</textarea></div>`;
  }
  const host = ideGraphHost();
  const graph = host ? `<section class="ide-mini"><div class="inline spread"><span class="nav-label">Graph</span></div><canvas id="graph3d" class="graph3d small" role="img" aria-label="This file's knowledge graph."></canvas>${host.node ? nodePanel() : ""}</section>` : "";
  return `${toolbar}<div class="ide-preview"><article class="prose">${mdHtml(stripFront(text))}</article>${graph}</div>`;
}

function ideEngine() {
  const ide = state.ide;
  const host = ide.engineHost;
  if (!host.graph) return '<div class="ide-preview"><p class="muted">Loading the graph…</p></div>';
  const chosen = ide.data.session_ids;
  const recordings = (state.sessions?.data || []).filter((item) => item.frames > 0).map((item) => `<label class="pick"><input type="checkbox" data-engine-session value="${item.id}" ${chosen.includes(item.id) ? "checked" : ""} /><span><strong>${esc(item.label || `Recording ${item.id}`)}</strong><span class="faint">${item.frames} frames</span></span></label>`).join("");
  const found = ide.engineFind;
  const results = found ? `<div class="engine-results">
    ${found.nodes.map((item) => `<button class="list-row" type="button" data-act="node" data-id="${esc(item.id)}"><span class="kind">${kindSwatch(item.kind)}${esc(item.kind)}</span><span><strong>${esc(item.label)}</strong></span><span class="num faint">${mins(item.seconds)}</span></button>`).join("")}
    ${found.events.map((event) => `<button class="list-row" type="button" ${event.frame_id ? `data-act="frame" data-id="${event.frame_id}"` : ""}><span class="kind">${esc(event.event_type)}</span><span>${esc(event.app_name || "")} · ${esc(snippet(event.text, 90))}</span><span class="num faint">${esc(clock(event.timestamp))}</span></button>`).join("")}
    ${found.nodes.length || found.events.length ? "" : '<p class="faint pad">No matches.</p>'}
  </div>` : "";
  return `<div class="ide-bar"><code class="ide-path">${ENGINE_PATH}</code></div>
    <div class="ide-preview engine">
      <form class="inline" data-form="engine-search"><input name="q" value="${esc(found?.q || "")}" placeholder="Search entities and system events" autocomplete="off" /><button class="btn" type="submit">Search</button></form>
      ${results}
      ${host.graph.nodes.length ? '<canvas id="graph3d" class="graph3d" role="img" aria-label="The harness knowledge graph. Drag to rotate, select a node to open it."></canvas>' : '<p class="muted pad">Pick a recording below to give this harness a graph.</p>'}
      ${host.node ? nodePanel() : ""}
      ${section("Built from", `<div class="pick-list">${recordings || '<p class="faint">No recordings yet.</p>'}</div>`)}
    </div>`;
}

function ideChatThread() {
  const ide = state.ide;
  const turns = [...ide.data.messages, ...(ide.pending ? [{ role: "user", text: ide.pending, edits: [] }] : [])];
  if (!turns.length) {
    const prompts = ["Make the workflows shorter and more specific", "What's missing from this harness?", "Add a rule to confirm before sending anything"];
    return `<div class="ide-prompts">${prompts.map((prompt) => `<button class="tag click" type="button" data-act="ide-prompt" data-q="${esc(prompt)}">${esc(prompt)}</button>`).join("")}</div>`;
  }
  const body = turns.map((turn) => {
    if (turn.role === "user") return `<div class="chat-q">${esc(turn.text)}</div>`;
    if (turn.role === "system") return `<p class="chat-sys">${esc(turn.text)}</p>`;
    const edits = (turn.edits || []).map((edit) => (edit.action === "failed"
      ? `<div class="edit-chip failed" title="${esc(edit.error || "")}"><span>failed</span><code>${esc(edit.path)}</code><em>${esc(edit.error || "")}</em></div>`
      : `<button class="edit-chip ${esc(edit.action)}" type="button" ${edit.action === "removed" ? "disabled" : `data-act="ide-open" data-path="${esc(edit.path)}"`}><span>${esc(edit.action)}</span><code>${esc(edit.path)}</code></button>`)).join("");
    return `<div class="chat-a prose">${mdHtml(turn.text)}${edits ? `<div class="edit-list">${edits}</div>` : ""}</div>`;
  }).join("");
  return body + (ide.busy ? '<div class="typing"><i></i><i></i><i></i></div>' : "");
}

function ideChat() {
  const ide = state.ide;
  const on = state.llm?.enabled;
  return `<aside class="ide-chat">
    <header><span class="nav-label">Chat</span><button class="btn sm" type="button" data-act="ide-undo" ${ide.data.versions ? "" : "disabled"}>Undo</button></header>
    <div id="ide-thread" class="ide-thread">${ideChatThread()}</div>
    ${on ? `<form class="ide-compose" data-form="ide-chat"><textarea id="ide-input" name="message" rows="3" placeholder="Ask for a change" ${ide.busy ? "disabled" : ""}></textarea><button class="btn sm primary" type="submit" ${ide.busy ? "disabled" : ""}>Send</button></form>` : '<p class="ide-compose faint">Add a model in <button class="link" type="button" data-act="nav" data-view="settings">Settings</button> to edit by chat. You can still edit in Source.</p>'}
  </aside>`;
}

function ideHtml() {
  const ide = state.ide;
  if (!ide.data) return '<p class="muted pad">Opening…</p>';
  const file = ideFile(ide.active);
  return `<div class="ide">
    ${ideExplorer()}
    <section class="ide-main">
      <nav class="ide-tabs">${ideTabs()}</nav>
      ${ide.active === ENGINE_PATH ? ideEngine() : file ? ideEditor(file) : '<div class="ide-preview muted">Choose a file.</div>'}
    </section>
    ${ideChat()}
  </div>`;
}

function syncIdeThread() {
  const thread = $("#ide-thread");
  if (!thread) return;
  thread.innerHTML = ideChatThread();
  thread.scrollTop = thread.scrollHeight;
}

async function ideOpen(path) {
  const ide = state.ide;
  if (!ide.open.includes(path)) ide.open.push(path);
  ide.active = path;
  ide.exportOpen = false;
  if (path === ENGINE_PATH && !ide.engineHost.graph) {
    paintView();
    ide.engineHost.graph = await api(`/harnesses/${ide.id}/engine`);
  }
  paintView();
}

async function ideSave() {
  const ide = state.ide;
  const path = ide.active;
  if (!ideDirty(path)) return;
  ide.data = await api(`/harnesses/${ide.id}/file`, { method: "PUT", body: JSON.stringify({ path, content: ide.drafts[path] }) });
  delete ide.drafts[path];
  toast("Saved");
  paintView();
}

async function ideSend(message) {
  const ide = state.ide;
  const cleaned = message.trim();
  if (!cleaned || ide.busy) return;
  ide.busy = true;
  ide.pending = cleaned;
  const box = $("#ide-input");
  if (box) box.value = "";
  paintView();
  syncIdeThread();
  try {
    const result = await api(`/harnesses/${ide.id}/chat`, { method: "POST", body: JSON.stringify({ message: cleaned }) });
    ide.data = result.harness;
    ide.engineHost.graph = ide.engineHost.graph ? await api(`/harnesses/${ide.id}/engine`) : null;
    ide.open = ide.open.filter((path) => path === ENGINE_PATH || ideFile(path));
    if (!ide.open.includes(ide.active)) ide.active = ide.open[0] || "AGENTS.md";
    const changed = result.edits.filter((edit) => edit.action === "edited" || edit.action === "added");
    if (changed.some((edit) => ideDirty(edit.path))) toast("You have unsaved edits in a file the model also changed");
    if (changed.length && !ide.open.includes(changed[0].path)) ide.open.push(changed[0].path);
    if (changed.length) ide.active = changed[0].path;
    ide.mode = "preview";
  } catch (error) {
    toast(error.message || "Request failed");
  } finally {
    ide.busy = false;
    ide.pending = null;
    paintView();
    syncIdeThread();
  }
}

/* Go to anything */

async function openPalette() {
  const [harnesses, sessions, saved, flows] = await Promise.all([api("/harnesses"), api("/sessions?limit=100"), api("/contexts?limit=300"), api("/workflows")]);
  const items = [
    { label: "New harness", kind: "command", run: () => actions["harness-new"]({ dataset: {} }) },
    { label: "New recording", kind: "command", run: () => go("recordings") },
    ...VIEWS.map((view) => ({ label: TITLES[view], kind: "page", run: () => go(view) })),
    ...harnesses.data.map((item) => ({ label: item.name, kind: "harness", run: () => go("harnesses", { ide: newIde(item.id) }) })),
    ...sessions.data.map((item) => ({ label: item.label || `Recording ${item.id}`, kind: "recording", run: () => openRecording(item.id) })),
    ...saved.data.map((item) => ({ label: item.title, kind: "context", run: () => openDoc(item.id) })),
    ...flows.data.map((item) => ({ label: item.title, kind: "workflow", run: () => openFlow(item.id) })),
  ];
  state.palette = { items, q: "", index: 0 };
  paintPalette();
  $("#palette-input")?.focus();
}

function paletteMatches() {
  const { items, q } = state.palette;
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter((item) => words.every((word) => `${item.label} ${item.kind}`.toLowerCase().includes(word))).slice(0, 12);
}

function paintPalette() {
  const node = $("#palette");
  if (!node) return;
  if (!state.palette) {
    node.innerHTML = "";
    return;
  }
  const rows = paletteMatches().map((item, index) => `<button class="pal-row ${index === state.palette.index ? "is-on" : ""}" type="button" data-act="palette-run" data-index="${index}"><span>${esc(item.label)}</span><em class="k-${esc(item.kind)}">${esc(item.kind)}</em></button>`).join("");
  const list = node.querySelector(".pal-list");
  if (list) {
    list.innerHTML = rows || '<p class="faint pad">Nothing matches.</p>';
    return;
  }
  node.innerHTML = `<div class="backdrop center top" data-act="palette-close"><div class="palette" data-sheet><input id="palette-input" placeholder="Go to a harness, recording, file, or page" autocomplete="off" /><div class="pal-list">${rows}</div></div></div>`;
}

function closePalette() {
  state.palette = null;
  paintPalette();
}

/* Integrations */

function clientDetail(item) {
  const selected = state.client;
  if (item.connected) {
    return `<div class="client-detail">
      
      ${selected.path ? `<p class="meta"><code>${esc(selected.path)}</code></p>` : ""}
      <div class="actions"><button class="btn sm danger" type="button" data-act="client-uninstall" data-id="${esc(item.id)}">Disconnect</button></div>
    </div>`;
  }
  return `<div class="client-detail">
    ${selected.path ? `<p class="meta"><code>${esc(selected.path)}</code></p>` : ""}
    <pre>${esc(selected.snippet)}</pre>
    <ol class="steps-list">${(selected.steps || []).map((step) => `<li>${esc(step)}</li>`).join("")}</ol>
    <div class="actions"><button class="btn sm" type="button" data-act="copy-snippet">Copy</button>${item.installable ? `<button class="btn sm primary" type="button" data-act="client-install" data-id="${esc(item.id)}">Install</button>` : ""}</div>
  </div>`;
}

function integrationsHtml() {
  const data = state.clients || { data: [] };
  const groups = { stdio: [], http: [] };
  for (const item of data.data) groups[item.transport].push(item);
  const tile = (item) => {
    const open = state.client?.id === item.id;
    const found = item.connected || item.detected;
    const flag = item.connected ? "connected" : item.detected ? "detected" : item.transport === "http" ? "remote" : "local";
    return `<article class="tile client-tile ${found ? "is-found" : ""} ${open ? "is-open" : ""}">
      <h3>${esc(item.label)}</h3>
      <span class="pill ${found ? "found" : ""}">${flag}</span>
      <button class="btn sm" type="button" data-act="client" data-id="${esc(item.id)}" aria-expanded="${open}">${open ? "Close" : item.connected ? "Disconnect" : "Connect"}</button>
      ${open ? clientDetail(item) : ""}
    </article>`;
  };
  const packages = PACKAGES.map((item) => `<article class="tile"><h3>${esc(item.name)}</h3><pre class="cmd">${esc(item.cmd)}</pre><button class="btn sm" type="button" data-act="copy-text" data-text="${esc(item.cmd)}">Copy</button></article>`).join("");
  const harnesses = state.harnesses || [];
  const chosen = harnesses.find((item) => String(item.id) === state.exportHarness) || harnesses[0];
  const href = (format) => media(`/harnesses/${chosen?.id}/export${qs({ format })}`);
  const fileLabels = { zip: ".zip", md: "One .md", json: "Graph JSON", cypher: "Cypher", graphml: "GraphML" };
  const destinations = DESTINATIONS.map((item) => `<tr><td><strong>${esc(item.name)}</strong><br><span class="faint">${esc(item.how)}</span></td><td class="right"><a class="btn sm" href="${href(item.format)}" download>${esc(fileLabels[item.format])}</a></td></tr>`).join("");
  return `<div class="stack">
    ${section("Agent providers over MCP", `      <p class="label">On this computer</p><div class="tile-grid">${groups.stdio.map(tile).join("")}</div>
      <p class="label">Remote and APIs</p><div class="tile-grid">${groups.http.map(tile).join("")}</div>`)}
    ${section("Packaged installs", `<div class="tile-grid wide">${packages}</div>`)}
    ${section(
      "Harnesses as files",
      chosen
        ? `
      <div class="inline"><label class="field">Harness<select data-change="export-harness">${harnesses.map((item) => `<option value="${item.id}" ${item.id === chosen.id ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label><a class="btn primary" href="${href("zip")}" download>Download .zip</a><a class="btn" href="${href("md")}" download>One .md file</a></div>
      <table class="table"><tbody>${destinations}</tbody></table>`
        : '<p class="muted">No harnesses yet.</p><div class="actions"><button class="btn" type="button" data-act="harness-new">New harness</button></div>',
    )}
  </div>`;
}

/* Settings */

function toggle(name, label, on) {
  return `<label class="check"><input type="checkbox" name="${name}" ${on ? "checked" : ""} /> ${esc(label)}</label>`;
}

function providerRow(item) {
  const llm = state.llm || {};
  const open = state.llmOpen === item.id;
  const local = !item.needs_key;
  const flag = item.active && llm.enabled ? '<span class="pill live">in use</span>' : item.has_key ? '<span class="pill ok">key saved</span>' : "";
  const test = state.llmTest?.[item.id];
  const models = (state.llmModels?.[item.id]?.models || []).map((name) => `<option value="${esc(name)}"></option>`).join("");
  const found = state.llmModels?.[item.id];
  const usable = local ? Boolean(item.model) : item.has_key;
  const keyHint = item.key_source === "saved" ? "Saved, type to replace" : item.key_source === "environment" ? `From ${item.env}, type to replace` : local ? "Optional" : "Paste your API key";
  return `<article class="provider ${item.active && llm.enabled ? "is-active" : ""} ${open ? "is-open" : ""}">
    <header>
      <button class="provider-name" type="button" data-act="llm-open" data-id="${item.id}" aria-expanded="${open}"><span>${esc(item.label)}</span>${item.model && !open ? `<code>${esc(item.model)}</code>` : ""}</button>
      ${flag}
      ${item.active && llm.enabled ? "" : `<button class="btn sm" type="button" data-act="llm-use" data-id="${item.id}" ${usable && !llm.locked_by_env ? "" : "disabled"}>Use</button>`}
    </header>
    ${open ? `<form class="stack provider-form" data-form="llm-save" data-id="${item.id}">
      ${local ? `<label class="field">Endpoint<input name="base_url" value="${esc(llm.base_url || "")}" placeholder="http://localhost:11434/v1" autocomplete="off" /></label>` : ""}
      <label class="field">API key<input name="api_key" type="password" placeholder="${esc(keyHint)}" autocomplete="off" /></label>
      <label class="field">Model<input name="model" list="models-${item.id}" value="${esc(item.model || "")}" placeholder="${esc(item.default_model || "llama3.2")}" autocomplete="off" /><datalist id="models-${item.id}">${models}</datalist></label>
      ${local && found?.error ? `<p class="faint">Nothing answered at this endpoint. Start Ollama or LM Studio, then test again.</p>` : ""}
      ${test ? `<p class="${test.ok ? "test-ok" : "test-bad"}">${test.ok ? `Connected to ${esc(test.model)}` : esc(test.error)}</p>` : ""}
      <div class="actions"><button class="btn primary sm" type="submit">Save</button><button class="btn sm" type="button" data-act="llm-test" data-id="${item.id}">Test</button>${item.key_source === "saved" ? `<button class="btn sm" type="button" data-act="llm-clear" data-id="${item.id}">Remove key</button>` : ""}</div>
    </form>` : ""}
  </article>`;
}

function modelSection() {
  const llm = state.llm || {};
  return section(
    "Model",
    `<div class="providers">${(llm.providers || []).map(providerRow).join("")}</div>
    <div class="inline spread"><p class="faint">Window titles, URLs, and redacted screen text go to the provider in use. A local model keeps them on this computer.</p>${llm.enabled ? '<button class="btn sm" type="button" data-act="llm-off">Turn off</button>' : ""}</div>
    ${llm.locked_by_env ? '<p class="banner">CHRONICLE_LLM=off is set, so models stay off on this computer.</p>' : ""}
    ${llm.last_error ? `<details class="fold"><summary>Last error</summary><p class="snippet">${esc(llm.last_error)}</p></details>` : ""}`,
  );
}

function settingsHtml() {
  const s = state.settings || {};
  return `<div class="stack narrow-page">
    ${section(
      "Capture",
      `<form class="stack" data-form="capture-settings">
        <div class="inline">
          <label class="field">Seconds between samples <input name="capture_interval_secs" type="number" min="1" max="600" value="${esc(s.capture_interval_secs)}" /></label>
          <label class="field">Idle after (seconds) <input name="idle_threshold_secs" type="number" min="15" value="${esc(s.idle_threshold_secs)}" /></label>
        </div>
        <div class="checks">${toggle("capture_screenshots", "Screenshots", s.capture_screenshots)}${toggle("capture_ocr", s.ocr_available ? "OCR when accessibility text is missing" : "OCR (needs tesseract: brew install tesseract)", s.capture_ocr)}${toggle("capture_clipboard", "Clipboard changes", s.capture_clipboard)}${toggle("redact", "Redact emails and secrets", s.redact)}</div>
        <div class="actions"><button class="btn primary" type="submit">Save capture settings</button></div>
      </form>`,
    )}
    ${section(
      "Retention",
      `<form class="inline" data-form="retention"><input name="days" class="narrow" type="number" min="0" value="${esc(s.retention_days)}" /><button class="btn" type="submit">Keep this many days</button></form>
      <p class="faint">Set to 0 to keep everything, otherwise older frames are deleted immediately.</p>`,
    )}
    ${modelSection()}
    ${section(
      "Data",
      `<p class="muted">Your data is stored in <code>${esc(s.home || "~/.chronicle")}</code> and uses ${bytes(state.health?.db_bytes)}.</p>
      <div class="actions"><button class="btn" type="button" data-act="demo">Load sample workday</button><button class="btn danger" type="button" data-act="demo-replace">Replace history with the sample</button></div>`,
    )}
  </div>`;
}

/* Mini window */

const mini = { win: null, busy: false, note: "" };

function miniHtml() {
  return `<div class="mini">
    <button class="mini-toggle" id="mini-toggle" type="button" data-act="mini-toggle"><svg viewBox="0 0 24 24" aria-hidden="true"><rect class="mini-stop" x="7" y="7" width="10" height="10" rx="1.5"/><circle class="mini-rec" cx="12" cy="12" r="6"/></svg></button>
    <div class="mini-text"><strong id="mini-status">Not recording</strong><span id="mini-meta"></span></div>
  </div>`;
}

function syncMini(root = mini.win?.document || (state.standalone ? document : null)) {
  const button = root?.getElementById("mini-toggle");
  if (!button) return;
  const recording = state.health?.recording;
  const label = recording ? "Stop recording" : "Start recording";
  button.classList.toggle("is-on", Boolean(recording));
  button.disabled = mini.busy;
  button.title = label;
  button.setAttribute("aria-label", label);
  root.getElementById("mini-status").textContent = recording
    ? `Recording · ${recording.remaining_secs != null ? countdown(recording.remaining_secs) : recording.elapsed}`
    : "Not recording";
  root.getElementById("mini-meta").textContent = mini.note || (recording ? `${recording.frames} frames` : "Ready");
}

async function miniTick() {
  try {
    state.health = await api("/health");
    syncMini();
  } catch {
    /* the next tick retries */
  }
}

async function miniToggle() {
  if (mini.busy) return;
  const recording = state.health?.recording;
  mini.busy = true;
  mini.note = recording ? "Building graph…" : "";
  syncMini();
  try {
    if (recording) await api(`/sessions/${recording.id}/stop`, { method: "POST" });
    else await api("/sessions", { method: "POST", body: JSON.stringify({ duration: null }) });
    mini.note = "";
  } catch (error) {
    mini.note = error.message || "Request failed";
  }
  mini.busy = false;
  await miniTick();
  if (!state.standalone && state.view === "recordings" && !state.ctx) await go("recordings");
}

async function openMini() {
  if (mini.win) {
    mini.win.focus();
    return;
  }
  let win = null;
  try {
    win = await window.documentPictureInPicture?.requestWindow({ width: 280, height: 84 });
  } catch {
    /* some embedded browsers expose the API but can't open the window */
  }
  if (!win) {
    const popup = window.open("/?mini=1", "chronicle-mini", "popup,width=280,height=110");
    toast(popup ? "This browser can't keep it on top of other windows" : "Allow pop-ups to open the mini window");
    return;
  }
  const sheet = win.document.createElement("link");
  sheet.rel = "stylesheet";
  sheet.href = document.querySelector('link[rel="stylesheet"]').href;
  win.document.head.append(sheet);
  win.document.title = "Chronicle";
  win.document.body.className = "mini-body";
  win.document.body.innerHTML = miniHtml();
  win.document.addEventListener("click", (event) => {
    if (event.target.closest("[data-act=mini-toggle]")) miniToggle();
  });
  // The main tab's timers slow down in the background, so the mini window keeps its own.
  const timer = win.setInterval(miniTick, 2000);
  win.addEventListener("pagehide", () => {
    win.clearInterval(timer);
    mini.win = null;
  });
  mini.win = win;
  syncMini();
}

async function bootMini() {
  state.standalone = true;
  document.body.className = "mini-body";
  document.title = "Chronicle";
  $("#app").innerHTML = miniHtml();
  await miniTick();
  setInterval(miniTick, 2000);
}

/* Painting */

function viewHtml() {
  const views = {
    harnesses: harnessesHtml,
    contexts: contextsHtml,
    workflows: workflowsHtml,
    integrations: integrationsHtml,
    settings: settingsHtml,
  };
  return (views[state.view] || recordingsHtml)();
}

function paintView() {
  const view = $("#view");
  const draft = $("#chat-input")?.value;
  const message = $("#ide-input")?.value;
  const scrolled = $(".ide-preview")?.scrollTop;
  const place = `${state.view}:${state.ctx?.id}:${state.ctx?.tab}:${state.doc?.id}:${state.flow?.id}:${state.ide?.id}:${state.wizard?.step}`;
  if (view) {
    view.innerHTML = viewHtml();
    view.classList.toggle("is-ide", Boolean(state.view === "harnesses" && state.ide));
    view.classList.toggle("enter", place !== state.place);
    state.place = place;
  }
  const input = $("#chat-input");
  if (input && draft) input.value = draft;
  const compose = $("#ide-input");
  if (compose && message) compose.value = message;
  const preview = $(".ide-preview");
  if (preview && scrolled) preview.scrollTop = scrolled;
  const thread = $("#chat-thread") || $("#ide-thread");
  if (thread) thread.scrollTop = thread.scrollHeight;
  $("#ide-new-title")?.focus();
  mountGraph();
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

async function finishOnboarding(start) {
  state.settings = await api("/settings", { method: "POST", body: JSON.stringify({ onboarded: true }) });
  if (start === "demo") {
    await api("/demo/load", { method: "POST" });
    toast("Sample workday loaded");
    await go("recordings", { landing: true });
    return;
  }
  if (start === "record") {
    await api("/sessions", { method: "POST", body: JSON.stringify({ duration: "15m", label: "First recording" }) });
    toast("Recording for 15 minutes");
    await go("recordings");
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
    if (event?.target?.closest("[data-sheet]") && !event.target.closest("button[data-act=close]")) return;
    closeDrawer();
  },
  async "open-recording"(button) {
    await openRecording(Number(button.dataset.id) || null, button.dataset.tab || "graph");
  },
  async "open-doc"(button) {
    await openDoc(Number(button.dataset.id));
  },
  async "open-flow"(button) {
    await openFlow(Number(button.dataset.id));
  },
  async "doc-delete"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Delete?";
      return;
    }
    await api(`/contexts/${button.dataset.id}`, { method: "DELETE" });
    toast("Context file deleted");
    await go("contexts");
  },
  async "flow-delete"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Delete?";
      return;
    }
    await api(`/workflows/${button.dataset.id}`, { method: "DELETE" });
    toast("Workflow deleted");
    await go("workflows");
  },
  async "recording-generate"(button) {
    button.disabled = true;
    button.textContent = "Writing…";
    const result = await api(`/sessions/${state.ctx.id}/generate`, { method: "POST" });
    state.ctx.artifacts = { contexts: result.contexts, workflows: result.workflows };
    const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
    toast(`Wrote ${plural(result.contexts_created, "context")} and ${plural(result.workflows_created, "workflow")}`);
    paintView();
  },
  async "harness-new"(button) {
    await go("harnesses", { wizard: newWizard(Number(button.dataset.session) || null) });
  },
  async "harness-open"(button) {
    await go("harnesses", { ide: newIde(Number(button.dataset.id)) });
  },
  async "harness-delete"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Delete harness?";
      return;
    }
    await api(`/harnesses/${button.dataset.id}`, { method: "DELETE" });
    toast("Harness deleted");
    await go("harnesses");
  },
  "wiz-example"(button) {
    state.wizard.name = button.dataset.name;
    paintView();
  },
  async "wiz-next"() {
    const wiz = state.wizard;
    if (wiz.step === 0 && !wiz.name.trim()) {
      wiz.note = "Give it a name first.";
      paintView();
      return;
    }
    wiz.note = "";
    if (wiz.step === 1) await wizardSuggest();
    wiz.step += 1;
    paintView();
    $(".main")?.scrollTo(0, 0);
  },
  "wiz-back"() {
    state.wizard.step -= 1;
    state.wizard.note = "";
    paintView();
  },
  async "wiz-go"(button) {
    const wiz = state.wizard;
    const step = Number(button.dataset.step);
    if (wiz.busy || step === wiz.step) return;
    if (!wiz.name.trim() && step > 0) {
      wiz.note = "Give it a name first.";
      paintView();
      return;
    }
    if (step > 1 && wiz.step <= 1) await wizardSuggest();
    wiz.step = step;
    wiz.note = "";
    paintView();
  },
  async "wiz-generate"() {
    const wiz = state.wizard;
    wiz.busy = true;
    wiz.note = "Writing contexts and workflows from your recordings. This can take a minute.";
    paintView();
    try {
      const missing = wiz.session_ids.filter((id) => !(state.saved || []).some((item) => item.session_id === id));
      await Promise.all(missing.map((id) => api(`/sessions/${id}/generate`, { method: "POST" })));
      await loadWizard();
      wiz.suggestKey = "";
      wiz.busy = false;
      await wizardSuggest();
    } finally {
      wiz.busy = false;
      paintView();
    }
  },
  "wiz-flow-add"() {
    state.wizard.new_workflows.push({ name: "", purpose: "" });
    paintView();
  },
  "wiz-flow-remove"(button) {
    state.wizard.new_workflows.splice(Number(button.dataset.index), 1);
    paintView();
  },
  async "wiz-build"() {
    const wiz = state.wizard;
    wiz.busy = true;
    const writing = wiz.new_workflows.filter((item) => item.name.trim());
    wiz.note = writing.length ? `Writing ${writing.length} workflow${writing.length === 1 ? "" : "s"} and AGENTS.md. This can take a minute.` : "Writing AGENTS.md.";
    paintView();
    try {
      const made = await api("/harnesses", {
        method: "POST",
        body: JSON.stringify({ name: wiz.name, purpose: wiz.purpose, session_ids: wiz.session_ids, context_ids: wiz.context_ids, workflow_ids: wiz.workflow_ids, new_workflows: writing }),
      });
      if (made.not_written.length) toast(`Your recordings don't show: ${made.not_written.join(", ")}`);
      await go("harnesses", { ide: newIde(made.id) });
    } catch (error) {
      wiz.busy = false;
      wiz.note = error.message || "Request failed";
      paintView();
    }
  },
  async "ide-open"(button) {
    await ideOpen(button.dataset.path);
  },
  "ide-close"(button) {
    const ide = state.ide;
    const path = button.dataset.path;
    ide.open = ide.open.filter((item) => item !== path);
    if (ide.active === path) ide.active = ide.open[ide.open.length - 1] || null;
    paintView();
  },
  "ide-mode"(button) {
    state.ide.mode = button.dataset.mode;
    paintView();
  },
  "ide-save"() {
    return ideSave();
  },
  "ide-add"(button) {
    state.ide.adding = state.ide.adding === button.dataset.kind ? null : button.dataset.kind;
    paintView();
  },
  "ide-export"() {
    state.ide.exportOpen = !state.ide.exportOpen;
    paintView();
  },
  async "ide-delete-file"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Remove file?";
      return;
    }
    const ide = state.ide;
    ide.data = await api(`/harnesses/${ide.id}/file${qs({ path: button.dataset.path })}`, { method: "DELETE" });
    ide.open = ide.open.filter((item) => item !== button.dataset.path);
    ide.active = ide.open[0] || "AGENTS.md";
    if (!ide.open.includes(ide.active)) ide.open.push(ide.active);
    paintView();
  },
  async "ide-undo"() {
    const ide = state.ide;
    ide.data = await api(`/harnesses/${ide.id}/undo`, { method: "POST" });
    ide.drafts = {};
    ide.open = ide.open.filter((path) => path === ENGINE_PATH || ideFile(path));
    if (!ide.open.includes(ide.active)) ide.active = "AGENTS.md";
    if (!ide.open.includes(ide.active)) ide.open.push(ide.active);
    paintView();
    syncIdeThread();
  },
  "ide-prompt"(button) {
    return ideSend(button.dataset.q);
  },
  "palette-open"() {
    return openPalette();
  },
  "palette-close"(button, event) {
    if (event?.target?.closest("[data-sheet]")) return;
    closePalette();
  },
  async "palette-run"(button) {
    const item = paletteMatches()[Number(button.dataset.index)];
    closePalette();
    await item?.run();
  },
  async "ctx-tab"(button) {
    state.ctx.tab = button.dataset.tab;
    if (state.ctx.tab === "graph" && !state.ctx.graph) await loadCtxGraph();
    if (state.ctx.tab === "contexts") await loadArtifacts();
    if (state.ctx.tab === "meetings" && !state.speakers) state.speakers = await api("/speakers/unnamed");
    history.replaceState(null, "", hashFor());
    paintView();
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
  async "chat-prompt"(button) {
    await sendChat(button.dataset.q);
    paintView();
  },
  "chat-clear"() {
    state.chats[chatKey()] = [];
    paintView();
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
    toast(`Recording ${done.id} stopped · ${done.frames} frames`);
    await openRecording(done.id);
  },
  async "session-delete"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Delete frames?";
      return;
    }
    await api(`/sessions/${button.dataset.id}`, { method: "DELETE" });
    await loadView("recordings");
    paint();
    toast("Recording deleted");
  },
  async node(button) {
    const host = graphHost();
    if (!host) return;
    if (state.ide) state.ide.engineFind = state.ide.active === ENGINE_PATH ? state.ide.engineFind : null;
    host.node = await api(`/graph/node${qs({ id: button.dataset.id, session_id: hostSession(host) })}`);
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
    toast("Context file written");
    await openDoc(made.id);
  },
  "graph-kind"(button) {
    state.ctx.graphKind = button.dataset.kind;
    paintView();
  },
  async "graph-tune"(button) {
    button.disabled = true;
    button.textContent = "Refining…";
    const session = ctxGraphSession();
    try {
      const result = await api("/graph/tune", { method: "POST", body: JSON.stringify({ session_id: session ? Number(session) : null }) });
      if (result.status === "error") throw new Error(result.error);
      await loadCtxGraph();
      state.ctx.node = null;
      toast(result.status === "up_to_date" ? "Already up to date" : `Applied ${result.applied} changes`);
    } finally {
      paintView();
    }
  },
  async "llm-open"(button) {
    const { id } = button.dataset;
    state.llmOpen = state.llmOpen === id ? null : id;
    state.llmModels = state.llmModels || {};
    paintView();
    if (state.llmOpen) {
      state.llmModels[id] = await api(`/llm/models${qs({ provider: id })}`);
      paintView();
    }
  },
  async "llm-use"(button) {
    state.llm = await api(`/llm/providers/${button.dataset.id}`, { method: "POST", body: JSON.stringify({ activate: true }) });
    toast(`Using ${state.llm.model}`);
    paintView();
  },
  async "llm-off"() {
    await api("/settings", { method: "POST", body: JSON.stringify({ llm_provider: "off" }) });
    state.llm = await api("/llm/status");
    paintView();
  },
  async "llm-clear"(button) {
    state.llm = await api(`/llm/providers/${button.dataset.id}`, { method: "POST", body: JSON.stringify({ clear_key: true }) });
    paintView();
  },
  async "llm-test"(button) {
    const { id } = button.dataset;
    await saveProvider(button.closest("form"), id);
    button.disabled = true;
    button.textContent = "Testing…";
    state.llmTest = { ...state.llmTest, [id]: await api(`/llm/test${qs({ provider: id })}`, { method: "POST" }) };
    state.llmModels[id] = await api(`/llm/models${qs({ provider: id })}`);
    state.llm = await api("/llm/status");
    paintView();
  },
  "mini-open"() {
    return openMini();
  },
  "mini-toggle"() {
    return miniToggle();
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
  async client(button) {
    const id = button.dataset.id;
    state.client = state.client?.id === id ? null : await api(`/clients/${encodeURIComponent(id)}`);
    paintView();
  },
  async "client-uninstall"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Remove from config?";
      return;
    }
    const result = await api(`/clients/${encodeURIComponent(button.dataset.id)}/uninstall`, { method: "POST" });
    state.clients = await api("/clients");
    state.client = null;
    paintView();
    toast(result.restart ? `Disconnected, restart ${result.restart} to finish` : "Disconnected");
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
    state.clients = await api("/clients");
    state.client = null;
    paintView();
    toast(result.path ? `Wrote ${result.path}, restart ${result.restart} to finish` : "Connected");
  },
  async demo() {
    const result = await api("/demo/load", { method: "POST" });
    toast(result.loaded ? "Sample workday loaded" : "History is not empty; use Replace in Settings");
    await go("recordings", { landing: Boolean(result.loaded) });
  },
  async "demo-replace"(button) {
    if (button.dataset.confirm !== "yes") {
      button.dataset.confirm = "yes";
      button.textContent = "Click again to delete all recordings";
      return;
    }
    await api("/demo/load?replace=true", { method: "POST" });
    toast("History replaced with the sample workday");
    await go("recordings", { landing: true });
  },
  async "context-new"(button) {
    const [sessions, saved, flows] = await Promise.all([api("/sessions?limit=200"), api("/contexts?limit=500"), api("/workflows")]);
    state.sessions = sessions;
    state.saved = saved.data;
    state.workflows = flows;
    $("#drawer").innerHTML = contextDialog(button.dataset.session || "");
    $("#drawer input[name=name]")?.focus();
  },
  async "workflow-new"(button) {
    const [sessions, saved, llm] = await Promise.all([api("/sessions?limit=200"), api("/contexts?limit=300"), api("/llm/status")]);
    state.sessions = sessions;
    state.saved = saved.data;
    state.llm = llm;
    $("#drawer").innerHTML = workflowDialog(button.dataset.session || "", button.dataset.context || "");
  },
  "pick-all"(button) {
    document.querySelectorAll(`.modal input[name=${button.dataset.name}]`).forEach((box) => {
      box.checked = true;
    });
  },
  "pick-none"(button) {
    document.querySelectorAll(`.modal input[name=${button.dataset.name}]`).forEach((box) => {
      box.checked = false;
    });
  },
};

const forms = {
  async "ide-chat"(form) {
    await ideSend(String(new FormData(form).get("message") || ""));
  },
  async "ide-new-file"(form) {
    const ide = state.ide;
    const made = await api(`/harnesses/${ide.id}/file`, { method: "POST", body: JSON.stringify({ kind: ide.adding, title: String(new FormData(form).get("title") || "") }) });
    ide.data = made;
    ide.adding = null;
    ide.mode = "source";
    await ideOpen(made.path);
  },
  async "engine-search"(form) {
    const ide = state.ide;
    const q = String(new FormData(form).get("q") || "");
    ide.engineFind = q ? await api(`/harnesses/${ide.id}/engine/search${qs({ q })}`) : null;
    paintView();
  },
  async chat(form) {
    await sendChat(String(new FormData(form).get("question") || ""));
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
    await loadView("recordings");
    paint();
    toast(duration === "open" ? "Recording until you stop it" : `Recording for ${duration}`);
  },
  async "context-new"(form) {
    const data = new FormData(form);
    const session_ids = data.getAll("session").map(Number);
    const context_ids = data.getAll("context").map(Number);
    const workflow_ids = data.getAll("workflow").map(Number);
    const note = form.querySelector("#context-note");
    if (!session_ids.length && !context_ids.length && !workflow_ids.length) {
      note.textContent = "Choose at least one recording, context, or workflow.";
      return;
    }
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "Generating…";
    note.textContent = "Reading what you chose. This can take a minute.";
    try {
      const made = await api("/contexts/compose", {
        method: "POST",
        body: JSON.stringify({ name: String(data.get("name") || ""), description: String(data.get("description") || ""), session_ids, context_ids, workflow_ids }),
      });
      toast(made.written_by ? "Context file written" : `Built without a model${made.writer_note ? `: ${made.writer_note}` : ""}`);
      await openDoc(made.id);
    } catch (error) {
      note.textContent = error.message || "Request failed";
      if (button.isConnected) {
        button.disabled = false;
        button.textContent = "Generate";
      }
    }
  },
  async "workflow-new"(form) {
    const data = new FormData(form);
    const session_ids = data.getAll("session").map(Number);
    const context_ids = data.getAll("context").map(Number);
    const note = form.querySelector("#workflow-note");
    if (!session_ids.length && !context_ids.length) {
      note.textContent = "Choose at least one recording or context file.";
      return;
    }
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    button.textContent = "Generating…";
    note.textContent = `Reading ${session_ids.length} recording${session_ids.length === 1 ? "" : "s"} and ${context_ids.length} context file${context_ids.length === 1 ? "" : "s"}. This can take a minute.`;
    try {
      const result = await api("/workflows/generate", {
        method: "POST",
        body: JSON.stringify({ name: String(data.get("name") || "") || null, purpose: String(data.get("purpose") || "") || null, session_ids, context_ids }),
      });
      if (result.status === "error") throw new Error(result.error);
      if (!result.created) {
        note.textContent = result.rejected.length ? `The model's draft didn't hold up: ${result.rejected[0].error}` : "The model didn't find this in what you chose. Try scanning more, or describe the job differently.";
        return;
      }
      toast(result.created === 1 ? "Workflow written" : `Wrote ${result.created} workflows`);
      if (result.created === 1) await openFlow(result.ids[0]);
      else await go("workflows");
    } catch (error) {
      note.textContent = error.message || "Request failed";
    } finally {
      if (button.isConnected) {
        button.disabled = false;
        button.textContent = "Generate";
      }
    }
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
  async "llm-save"(form) {
    await saveProvider(form, form.dataset.id);
    state.llmTest = { ...state.llmTest, [form.dataset.id]: null };
    paintView();
    toast("Saved");
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
};

async function saveProvider(form, id) {
  const data = new FormData(form);
  const payload = { model: String(data.get("model") || "") };
  if (data.has("base_url")) payload.base_url = String(data.get("base_url"));
  const key = String(data.get("api_key") || "").trim();
  if (key) payload.api_key = key;
  // With no model in use yet, the first one set up becomes the one in use.
  if (!state.llm?.enabled && (key || id === "local")) payload.activate = Boolean(key || payload.model);
  state.llm = await api(`/llm/providers/${id}`, { method: "POST", body: JSON.stringify(payload) });
}

const changes = {
  "export-harness"(select) {
    state.exportHarness = select.value;
    paintView();
  },
};

document.addEventListener("click", async (event) => {
  if (event.target.closest("a[href]")) return;
  const button = event.target.closest("[data-act]");
  if (!button || button.disabled) return;
  const act = actions[button.dataset.act];
  if (!act) return;
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

document.addEventListener("change", async (event) => {
  const target = event.target;
  const handler = changes[target?.dataset?.change];
  if (handler) handler(target);
  if (target?.dataset?.wizPick) {
    const list = state.wizard[target.dataset.wizPick];
    const id = Number(target.value);
    state.wizard[target.dataset.wizPick] = target.checked ? [...list, id] : list.filter((item) => item !== id);
    target.closest(".pick")?.classList.toggle("is-on", target.checked);
  }
  if ("engineSession" in (target?.dataset || {})) {
    const ide = state.ide;
    const ids = [...document.querySelectorAll("[data-engine-session]:checked")].map((box) => Number(box.value));
    try {
      ide.data = await api(`/harnesses/${ide.id}`, { method: "PUT", body: JSON.stringify({ session_ids: ids }) });
      ide.engineHost = { graph: await api(`/harnesses/${ide.id}/engine`), node: null };
      paintView();
    } catch (error) {
      toast(error.message || "Request failed");
    }
  }
});

document.addEventListener("input", (event) => {
  const target = event.target;
  if (target.dataset?.wiz) state.wizard[target.dataset.wiz] = target.value;
  if (target.dataset?.wizFlow) state.wizard.new_workflows[Number(target.dataset.wizFlow)][target.dataset.key] = target.value;
  if (target.id === "ide-text") {
    const ide = state.ide;
    ide.drafts[ide.active] = target.value;
    const gutter = $("#ide-gutter");
    if (gutter) gutter.textContent = target.value.split("\n").map((_, index) => index + 1).join("\n");
    const bar = target.closest(".ide-main");
    if (ideDirty(ide.active) && !bar.querySelector("[data-act=ide-save]")) {
      const scroll = target.scrollTop;
      const { selectionStart, selectionEnd } = target;
      paintView();
      const next = $("#ide-text");
      next.focus();
      next.setSelectionRange(selectionStart, selectionEnd);
      next.scrollTop = scroll;
    }
  }
  if (target.id === "palette-input") {
    state.palette.q = target.value;
    state.palette.index = 0;
    paintPalette();
  }
});

document.addEventListener("scroll", (event) => {
  if (event.target?.id === "ide-text") {
    const gutter = $("#ide-gutter");
    if (gutter) gutter.scrollTop = event.target.scrollTop;
  }
}, true);

document.addEventListener("keydown", (event) => {
  const command = event.metaKey || event.ctrlKey;
  if (command && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (state.palette) closePalette();
    else if (state.settings?.onboarded) openPalette().catch((error) => toast(error.message || "Request failed"));
    return;
  }
  if (state.palette) {
    const count = paletteMatches().length;
    if (event.key === "Escape") closePalette();
    else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      state.palette.index = (state.palette.index + (event.key === "ArrowDown" ? 1 : count - 1)) % Math.max(1, count);
      paintPalette();
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = paletteMatches()[state.palette.index];
      closePalette();
      item?.run();
    }
    return;
  }
  if (command && event.key.toLowerCase() === "s" && state.ide) {
    event.preventDefault();
    ideSave().catch((error) => toast(error.message || "Request failed"));
    return;
  }
  if (event.target.id === "ide-input" && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    event.target.closest("form").requestSubmit();
    return;
  }
  if (event.target.id === "ide-text" && event.key === "Tab") {
    event.preventDefault();
    event.target.setRangeText("  ", event.target.selectionStart, event.target.selectionEnd, "end");
    event.target.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
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

window.addEventListener("beforeunload", (event) => {
  if (state.ide && Object.keys(state.ide.drafts).some(ideDirty)) event.preventDefault();
});

async function boot() {
  if (new URLSearchParams(location.search).has("mini")) {
    await bootMini();
    return;
  }
  paint();
  try {
    state.settings = await api("/settings");
    state.health = await api("/health");
    if (state.settings.onboarded) {
      readHash();
      try {
        await loadView(state.view);
      } catch (error) {
        state.view = "recordings";
        state.ctx = null;
        await loadView("recordings");
        toast(error.message || "Could not open that page");
      }
    }
  } catch (error) {
    state.bootError = error.message || "The local engine is not reachable.";
  }
  state.ready = true;
  paint();
  startPolling();
  if (state.settings?.onboarded && !state.llm) {
    state.llm = await api("/llm/status").catch(() => null);
    syncChrome();
  }
}

boot();
