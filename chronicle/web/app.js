const VIEWS = ["home", "record", "graph", "context", "connect", "timeline", "search", "meetings", "pipes", "workflows", "settings"];
const TITLES = {
  home: "Home",
  record: "Record",
  graph: "Knowledge graph",
  context: "Agent context",
  connect: "Connect an agent",
  timeline: "Timeline",
  search: "Search",
  meetings: "Meetings",
  pipes: "Pipes",
  workflows: "Workflows",
  settings: "Settings",
};
const COLORS = {
  Slack: "#e01e5a",
  Zoom: "#2d8cff",
  Linear: "#5e6ad2",
  Cursor: "#c4a484",
  Chrome: "#e0a15a",
  Arc: "#f25c54",
  Safari: "#4aa3df",
  Figma: "#f24e1e",
  Gmail: "#e07a6a",
  Notion: "#d9d3c5",
  Terminal: "#7dcea0",
};
const KIND_COLORS = {
  app: "#e2f07a",
  window: "#6d6a60",
  site: "#4aa3df",
  page: "#7fb8e0",
  query: "#e8c547",
  person: "#f2a65a",
  project: "#b48ef0",
  customer: "#e07a9a",
  ticket: "#7dcea0",
  pr: "#5ec2b7",
  file: "#c4a484",
  identifier: "#a9a395",
  meeting: "#2d8cff",
  topic: "#d9d3c5",
  session: "#ffffff",
};
const PRESETS = ["5m", "15m", "30m", "1h", "2h", "4h"];
const PROMPTS = [
  "What was I working on today?",
  "Where did we land on the Acme export columns?",
  "Summarize my meetings",
  "What should I automate?",
];

const state = {
  ready: false,
  view: "home",
  step: 0,
  settings: null,
  health: null,
  timeline: null,
  activity: null,
  meetings: null,
  speakers: null,
  pipes: null,
  workflows: null,
  ai: null,
  memories: null,
  search: null,
  sessions: null,
  capture: null,
  graph: null,
  graphScope: "",
  graphKind: "",
  graphQuery: "",
  node: null,
  context: null,
  contextTask: "",
  contextScope: "",
  contextMinutes: "",
  clients: null,
  client: null,
  query: "",
  contentType: "all",
  appName: "",
  date: "",
  selected: null,
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

function color(app) {
  return COLORS[app] || "#8d887c";
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

function minutesOfDay(iso) {
  const date = new Date(iso);
  return date.getHours() * 60 + date.getMinutes();
}

function todayKey() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function dayLabel(isoDate) {
  if (!isoDate) return "";
  if (isoDate === todayKey()) return "Today";
  const date = new Date(`${isoDate}T12:00:00`);
  return date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
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

async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (opts.body) opts.headers["Content-Type"] = "application/json";
  if (token()) opts.headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(path, opts);
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { error: text };
    }
  }
  if (!response.ok) {
    if (response.status === 401) {
      throw new Error("This server needs a token. Open it with ?token=YOUR_TOKEN once.");
    }
    throw new Error(body?.error || `Request failed (${response.status})`);
  }
  return body;
}

function scopeParam(value) {
  return value ? `session_id=${encodeURIComponent(value)}` : "";
}

function closeDrawer() {
  const drawer = $("#drawer");
  if (drawer) drawer.innerHTML = "";
}

async function loadGraph() {
  const scope = scopeParam(state.graphScope);
  state.graph = await api(`/graph?limit=140${scope ? `&${scope}` : ""}`);
  state.graphSummary = await api(`/graph/summary${scope ? `?${scope}` : ""}`);
}

async function loadView(view) {
  if (view === "home" || view === "timeline" || (view === "search" && !state.timeline)) {
    const query = state.date ? `?date=${encodeURIComponent(state.date)}` : "";
    state.timeline = await api(`/timeline${query}`);
    state.date = state.timeline.date;
    if (view !== "search") state.activity = await api("/activity-summary");
  }
  if (view === "home") {
    state.memories = await api("/memories?limit=5");
    state.sessions = await api("/sessions?limit=5");
  }
  if (view === "record") {
    state.sessions = await api("/sessions?limit=50");
    state.capture = await api("/capture/status");
  }
  if (view === "graph" || view === "context") {
    state.sessions = await api("/sessions?limit=50");
  }
  if (view === "graph") await loadGraph();
  if (view === "connect" && !state.clients) state.clients = await api("/clients");
  if (view === "meetings") {
    state.meetings = await api("/meetings");
    state.speakers = await api("/speakers/unnamed");
  }
  if (view === "pipes") state.pipes = await api("/pipes");
  if (view === "workflows") state.workflows = await api("/workflows");
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
          state.sessions = await api("/sessions?limit=50");
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
  history.replaceState(null, "", `#${view}`);
  await loadView(view);
  paint();
}

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
      <p class="kicker">${esc(step.kicker)}</p>
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
    <p class="kicker">Start</p>
    <h1>How do you want to begin?</h1>
    <p class="lead">A recording runs for a preset time, then its graph is built. On macOS, the app running Chronicle needs Screen Recording and Accessibility permission. The sample workday lets you try the graph and MCP tools without recording anything.</p>
    <div class="plans">
      <article class="plan"><h2>Record 15 minutes</h2><p class="muted">Do your normal work. The graph is ready when the timer ends.</p><button class="btn primary" type="button" data-act="finish" data-start="record">Start recording</button></article>
      <article class="plan"><h2>Sample workday</h2><p class="muted">18 frames, 2 meetings, a ticket, a PR, and a customer deadline.</p><button class="btn" type="button" data-act="finish" data-start="demo">Load sample</button></article>
      <article class="plan"><h2>Start empty</h2><p class="muted">Connect an agent first and let it call start_recording.</p><button class="btn" type="button" data-act="finish" data-start="empty">Open Chronicle</button></article>
    </div>
    <div class="actions"><button class="btn" type="button" data-act="back">Back</button></div>
  </div></section>`;
}

function shellHtml() {
  const buttons = VIEWS.map(
    (view) => `<button class="nav-btn" type="button" data-act="nav" data-view="${view}">${TITLES[view]}</button>`,
  ).join("");
  return `<div class="shell">
    <aside class="rail">
      <div class="brand"><div class="mark"></div><div><strong>Chronicle</strong><span>context for agents</span></div></div>
      <button class="rec" type="button" data-act="nav" data-view="record"><i id="rec-dot" class="dot"></i><span id="rec-label">Not recording</span></button>
      <nav class="nav">${buttons}</nav>
      <div class="rail-foot"><div id="graph-count"></div><div id="frame-count"></div><a href="https://modelcontextprotocol.io" target="_blank" rel="noreferrer">MCP</a> · <a href="/docs">API</a></div>
    </aside>
    <main class="main">
      <header class="top"><h1 id="crumb">Home</h1><p id="subhead"></p></header>
      <div id="view"></div>
    </main>
  </div>
  <div id="drawer"></div>`;
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
        ? `Recording · ${countdown(recording.remaining_secs)} left`
        : `Recording · ${recording.elapsed}`
      : "Not recording";
  }
  const count = $("#frame-count");
  if (count && state.health) count.textContent = `${state.health.frames} frames · ${bytes(state.health.db_bytes)}`;
  const graph = $("#graph-count");
  if (graph && state.health) graph.textContent = `${state.health.graph_nodes} nodes · ${state.health.graph_edges} edges`;
  const crumb = $("#crumb");
  if (crumb) crumb.textContent = TITLES[state.view] || "Home";
  const sub = $("#subhead");
  if (sub) sub.textContent = state.view === "timeline" && state.timeline ? dayLabel(state.timeline.date) : "";
}

function emptyHtml() {
  return `<section class="card hero">
    <h2>Nothing recorded yet</h2>
    <p class="muted">Record a stretch of work, or load the sample workday to try the graph and the MCP tools first.</p>
    <div class="actions">
      <button class="btn primary" type="button" data-act="nav" data-view="record">Record</button>
      <button class="btn" type="button" data-act="demo">Load sample workday</button>
      <button class="btn" type="button" data-act="nav" data-view="connect">Connect an agent</button>
    </div>
  </section>`;
}

function homeHtml() {
  if (!state.health?.frames) return `<div class="stack">${emptyHtml()}</div>`;
  const activity = state.activity || { apps: [], narrative: "", meetings: [] };
  const max = Math.max(1, ...activity.apps.map((app) => app.seconds || 0));
  const bars = activity.apps
    .slice(0, 6)
    .map(
      (app) => `<div class="bar-row"><span>${esc(app.app_name)}</span><div class="track"><span style="width:${Math.round(((app.seconds || 0) / max) * 100)}%;background:${color(app.app_name)}"></span></div><span>${mins(app.seconds)}</span></div>`,
    )
    .join("");
  const frames = state.timeline?.frames || [];
  const film = frames
    .map((frame) => {
      const shot = frame.media_url
        ? `<img src="${esc(media(frame.media_url))}" alt="${esc(frame.app_name)} ${esc(frame.window_name)}" loading="lazy" />`
        : `<div class="ph" style="background:${color(frame.app_name)}">${esc(frame.app_name)}</div>`;
      return `<button class="thumb" type="button" data-act="frame" data-id="${frame.frame_id}">${shot}<span>${esc(clock(frame.timestamp))} ${esc(frame.app_name)}</span></button>`;
    })
    .join("");
  const prompts = PROMPTS.map(
    (prompt) => `<button class="chip" type="button" data-act="prompt" data-q="${esc(prompt)}">${esc(prompt)}</button>`,
  ).join("");
  const recordings = (state.sessions?.data || [])
    .slice(0, 4)
    .map(
      (item) => `<div class="row static"><span class="kind">${esc(item.status)}</span><span><strong>${esc(item.label || `Recording ${item.id}`)}</strong><br><span class="faint">${esc(stamp(item.started_at))} · ${esc(item.elapsed)} · ${item.frames} frames</span></span><span class="actions tight"><button class="btn" type="button" data-act="session-graph" data-id="${item.id}">Graph</button><button class="btn" type="button" data-act="session-context" data-id="${item.id}">Context</button></span></div>`,
    )
    .join("");
  return `<div class="stack">
    <section class="card"><p class="narrative">${esc(activity.narrative || "Nothing is stored for today.")}</p></section>
    <form class="ask" data-form="ask">
      <input name="question" placeholder="Ask what you saw, heard, or still owe" autocomplete="off" />
      <button class="btn primary" type="submit">Ask</button>
    </form>
    <div class="chips">${prompts}</div>
    <div id="answer" class="card" hidden></div>
    <div class="grid-2">
      <section class="card"><h2>Where the time went</h2><div class="bars">${bars || '<p class="muted">No frames today.</p>'}</div></section>
      <section class="card"><h2>Recordings</h2>${recordings || '<p class="muted">No recordings yet.</p>'}<div class="actions"><button class="btn" type="button" data-act="nav" data-view="record">Record</button></div></section>
    </div>
    <section class="card"><h2>Frames</h2><div class="film">${film || '<p class="muted">No frames on this day.</p>'}</div></section>
  </div>`;
}

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
    .map(([key, label]) => `<span class="chip ${caps[key] ? "ok" : "off"}">${caps[key] ? "✓" : "–"} ${esc(label)}</span>`)
    .join("");
  const live = active
    ? `<section class="card live">
        <p class="kicker">Recording ${active.id}${active.label ? ` · ${esc(active.label)}` : ""}</p>
        <div class="big" id="live-clock">${active.remaining_secs != null ? countdown(active.remaining_secs) : esc(active.elapsed)}</div>
        <p class="meta" id="live-meta">${active.remaining_secs != null ? "left" : "elapsed, until stopped"} · ${active.frames} frames · ${active.events} events · every ${active.interval_secs}s</p>
        <div class="actions"><button class="btn danger" type="button" data-act="session-stop" data-id="${active.id}">Stop and build graph</button></div>
      </section>`
    : `<section class="card">
        <h2>New recording</h2>
        <p class="muted">Pick how long to record. Frames and system events are tied to this recording, and its graph is built when the time runs out.</p>
        <form class="stack" data-form="record">
          <div class="chips">${PRESETS.map((value, index) => `<label class="chip pick"><input type="radio" name="duration" value="${value}" ${index === 2 ? "checked" : ""} />${value}</label>`).join("")}<label class="chip pick"><input type="radio" name="duration" value="open" />Until stopped</label></div>
          <div class="inline">
            <input name="label" placeholder="What are you about to do? (optional)" autocomplete="off" />
            <input name="interval" type="number" min="1" max="600" value="${esc(capture.interval_secs || 5)}" title="Seconds between samples" />
            <button class="btn primary" type="submit">Start recording</button>
          </div>
        </form>
      </section>`;
  const rows = (state.sessions?.data || [])
    .map(
      (item) => `<div class="row static"><span class="kind">${esc(item.status)}</span><span><strong>${esc(item.label || `Recording ${item.id}`)}</strong><br><span class="faint">${esc(stamp(item.started_at))} · ${esc(item.elapsed)} · ${item.frames} frames · ${item.events} events</span></span><span class="actions tight">${
        item.status === "recording"
          ? `<button class="btn danger" type="button" data-act="session-stop" data-id="${item.id}">Stop</button>`
          : `<button class="btn" type="button" data-act="session-graph" data-id="${item.id}">Graph</button><button class="btn" type="button" data-act="session-context" data-id="${item.id}">Context</button><button class="btn" type="button" data-act="session-delete" data-id="${item.id}">Delete</button>`
      }</span></div>`,
    )
    .join("");
  return `<div class="stack">
    ${live}
    ${capture.last_error ? `<p class="banner">${esc(capture.last_error)}</p>` : ""}
    <section class="card">
      <h2>This machine</h2>
      <div class="chips">${capRow}</div>
      <p class="faint">Never recorded: keystrokes. Skipped apps: ${esc((capture.denied_apps || []).join(", "))}. ${capture.recorder ? `Recorder process: ${esc(capture.recorder)}.` : ""}</p>
      <p class="faint">CLI: <code>chronicle record 30m --label "..."</code>. Agents can call <code>start_recording</code> over MCP.</p>
    </section>
    <section class="card"><h2>Recordings</h2><div class="rows">${rows || '<p class="muted">No recordings yet.</p>'}</div></section>
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

function scopeOptions(selected) {
  const options = [`<option value="">All history</option>`].concat(
    (state.sessions?.data || []).map(
      (item) => `<option value="${item.id}" ${String(item.id) === String(selected) ? "selected" : ""}>Recording ${item.id}${item.label ? ` · ${esc(item.label)}` : ""} (${esc(item.elapsed)})</option>`,
    ),
  );
  return options.join("");
}

function layout(nodes, edges, width, height) {
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const points = nodes.map((node, i) => {
    const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
    const radius = 0.3 + 0.1 * ((i * 7919) % 10) / 10;
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

function graphSvg() {
  const graph = state.graph || { nodes: [], edges: [] };
  const hidden = new Set(state.graphKind ? [] : ["window"]);
  const nodes = graph.nodes.filter((node) => (state.graphKind ? node.kind === state.graphKind || node.id === state.node?.node?.id : !hidden.has(node.kind)));
  const ids = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => ids.has(edge.src) && ids.has(edge.dst) && edge.rel !== "has_window");
  if (!nodes.length) return '<p class="muted">No graph nodes in this scope.</p>';
  const width = 900;
  const height = 560;
  const { points, links } = layout(nodes, edges, width, height);
  const maxWeight = Math.max(1, ...nodes.map((node) => node.seconds || node.weight || 1));
  const selected = state.node?.node?.id;
  const neighbors = new Set((state.node?.neighbors || []).map((item) => item.id));
  const lines = links
    .map(([a, b, weight]) => {
      const lit = selected && (nodes[a].id === selected || nodes[b].id === selected);
      return `<line x1="${points[a].x.toFixed(1)}" y1="${points[a].y.toFixed(1)}" x2="${points[b].x.toFixed(1)}" y2="${points[b].y.toFixed(1)}" class="${lit ? "lit" : ""}" stroke-width="${Math.min(3, 0.4 + Math.log1p(weight) * 0.5).toFixed(2)}" />`;
    })
    .join("");
  const ranked = [...nodes].sort((x, y) => (y.seconds || y.weight) - (x.seconds || x.weight)).slice(0, 28).map((node) => node.id);
  const labelled = new Set(ranked);
  const circles = nodes
    .map((node, i) => {
      const size = 4 + Math.sqrt((node.seconds || node.weight || 1) / maxWeight) * 14;
      const dim = selected && node.id !== selected && !neighbors.has(node.id) ? "dim" : "";
      const label = labelled.has(node.id) || node.id === selected || neighbors.has(node.id)
        ? `<text x="${(points[i].x + size + 3).toFixed(1)}" y="${(points[i].y + 4).toFixed(1)}">${esc(snippet(node.label, 28))}</text>`
        : "";
      return `<g class="gnode ${dim} ${node.id === selected ? "is-on" : ""}" data-act="node" data-id="${esc(node.id)}"><title>[${esc(node.kind)}] ${esc(node.label)}</title><circle cx="${points[i].x.toFixed(1)}" cy="${points[i].y.toFixed(1)}" r="${size.toFixed(1)}" fill="${KIND_COLORS[node.kind] || "#8d887c"}" />${label}</g>`;
    })
    .join("");
  return `<svg class="graph" viewBox="0 0 ${width} ${height}" role="img" aria-label="Knowledge graph"><g class="edges">${lines}</g>${circles}</svg>`;
}

function nodePanel() {
  const data = state.node;
  if (!data) {
    return `<section class="card"><h2>Pick a node</h2><p class="muted">Click a circle to see what it connects to and which frames it came from. Window nodes are hidden unless you filter by kind.</p></section>`;
  }
  const node = data.node;
  const evidence = (node.evidence || [])
    .map((item) =>
      item.type === "frame"
        ? `<button class="chip" type="button" data-act="frame" data-id="${item.id}">frame ${item.id} · ${esc(clock(item.timestamp))}</button>`
        : `<span class="chip">${esc(item.type)} ${item.id} · ${esc(clock(item.timestamp))}</span>`,
    )
    .join("");
  const rows = (data.neighbors || [])
    .map(
      (item) => `<button class="row" type="button" data-act="node" data-id="${esc(item.id)}"><span class="kind" style="color:${KIND_COLORS[item.kind] || "inherit"}">${esc(item.kind)}</span><span>${esc(item.direction === "out" ? "→" : "←")} <em>${esc(item.rel)}</em> · <strong>${esc(item.label)}</strong></span><span class="faint">${Math.round(item.weight)}</span></button>`,
    )
    .join("");
  return `<section class="card">
    <p class="kicker" style="color:${KIND_COLORS[node.kind] || "inherit"}">${esc(node.kind)}</p>
    <h2>${esc(node.label)}</h2>
    <p class="meta"><code>${esc(node.id)}</code> · seen ${Math.round(node.weight)}× · ${mins(node.seconds)} on screen · ${esc(stamp(node.first_seen))} – ${esc(stamp(node.last_seen))}</p>
    <div class="chips">${evidence}</div>
    <div class="rows">${rows || '<p class="muted">No neighbors.</p>'}</div>
    <div class="actions"><button class="btn" type="button" data-act="node-context" data-label="${esc(node.label)}">Context for this</button></div>
  </section>`;
}

function graphHtml() {
  const summary = state.graphSummary || { counts: {}, transitions: [] };
  const kinds = Object.entries(summary.counts || {})
    .map(
      ([kind, count]) => `<button class="chip ${state.graphKind === kind ? "is-on" : ""}" type="button" data-act="graph-kind" data-kind="${esc(kind)}"><i class="swatch" style="background:${KIND_COLORS[kind] || "#8d887c"}"></i>${esc(kind)} ${count}</button>`,
    )
    .join("");
  const transitions = (summary.transitions || [])
    .slice(0, 8)
    .map((item) => `<div class="line">${esc(item.from)} → ${esc(item.to)} <span class="faint">${Math.round(item.count)}×</span></div>`)
    .join("");
  const results = state.graphResults
    ? `<div class="rows">${state.graphResults
        .map((item) => `<button class="row" type="button" data-act="node" data-id="${esc(item.id)}"><span class="kind" style="color:${KIND_COLORS[item.kind] || "inherit"}">${esc(item.kind)}</span><span><strong>${esc(item.label)}</strong></span><span class="faint">${mins(item.seconds)}</span></button>`)
        .join("") || '<p class="muted">No entity matches.</p>'}</div>`
    : "";
  return `<div class="stack">
    <form class="searchbar" data-form="graph">
      <select name="scope">${scopeOptions(state.graphScope)}</select>
      <input name="q" value="${esc(state.graphQuery)}" placeholder="Find an entity: BILL-142, export.ts, Maya…" autocomplete="off" />
      <button class="btn primary" type="submit">Find</button>
      <button class="btn" type="button" data-act="graph-build">Rebuild</button>
    </form>
    <div class="chips">${kinds}${state.graphKind ? '<button class="chip" type="button" data-act="graph-kind" data-kind="">Show all</button>' : ""}</div>
    ${results}
    <div class="graph-grid">
      <section class="card graph-card">${graphSvg()}<p class="faint">${esc(summary.scope || "all")} · built ${esc(stamp(summary.built_at) || "never")} · ${state.graph?.nodes?.length || 0} heaviest nodes shown</p></section>
      <div class="stack">${nodePanel()}${transitions ? `<section class="card"><h2>Habitual transitions</h2>${transitions}</section>` : ""}</div>
    </div>
  </div>`;
}

function contextHtml() {
  const pack = state.context;
  const body = pack
    ? `<section class="card">
        <div class="split"><p class="meta">${pack.markdown.length.toLocaleString()} characters · about ${Math.round(pack.markdown.length / 4).toLocaleString()} tokens · ${pack.graph.nodes.length} graph nodes · ${pack.evidence.length} evidence items</p><button class="btn" type="button" data-act="copy-context">Copy markdown</button></div>
        <pre class="context">${esc(pack.markdown)}</pre>
      </section>`
    : `<section class="card"><p class="muted">This is exactly what an agent receives from the <code>get_context</code> MCP tool. Describe a task to focus the graph and the evidence on it.</p></section>`;
  return `<div class="stack">
    <form class="searchbar" data-form="context">
      <input name="task" value="${esc(state.contextTask)}" placeholder="Task for the agent, e.g. finish the Acme export" autocomplete="off" />
      <select name="scope">${scopeOptions(state.contextScope)}</select>
      <input name="minutes" type="number" min="1" value="${esc(state.contextMinutes)}" placeholder="last N min" title="Only the last N minutes (all history when empty)" />
      <button class="btn primary" type="submit">Build context</button>
    </form>
    ${body}
  </div>`;
}

function connectHtml() {
  const data = state.clients || { data: [] };
  const groups = { stdio: [], http: [] };
  for (const item of data.data) groups[item.transport].push(item);
  const card = (item) => `<button class="connect ${state.client?.id === item.id ? "is-on" : ""}" type="button" data-act="client" data-id="${esc(item.id)}"><h2>${esc(item.label)}</h2><p class="meta">${item.detected ? "detected on this machine" : item.transport === "http" ? "remote · Streamable HTTP" : "local · stdio"}</p></button>`;
  const selected = state.client;
  const detail = selected
    ? `<section class="card">
        <div class="split"><h2>${esc(selected.label)}</h2><div class="actions tight"><button class="btn" type="button" data-act="copy-snippet">Copy</button>${
          (data.data.find((item) => item.id === selected.id) || {}).installable
            ? `<button class="btn primary" type="button" data-act="client-install" data-id="${esc(selected.id)}">Install</button>`
            : ""
        }</div></div>
        ${selected.path ? `<p class="meta">${esc(selected.path)}</p>` : ""}
        <pre class="context">${esc(selected.snippet)}</pre>
        <ol class="steps-list">${(selected.steps || []).map((step) => `<li>${esc(step)}</li>`).join("")}</ol>
        <p class="faint">Install merges this server into the client's config and keeps a <code>.chronicle-backup</code> copy of the old file. Same as <code>chronicle mcp install ${esc(selected.id)}</code>.</p>
      </section>`
    : `<section class="card"><p class="muted">Pick a client to get its config. Local clients start <code>chronicle mcp</code> over stdio. Remote clients (ChatGPT, the OpenAI and Anthropic APIs) reach this server at <code>${esc(data.mcp_url || "/mcp")}</code>, which must be public HTTPS.</p></section>`;
  return `<div class="stack">
    <section class="card">
      <h2>One server, every agent</h2>
      <p class="muted">Chronicle speaks the Model Context Protocol. Tools: <code>get_context</code>, <code>search_history</code>, <code>query_graph</code>, <code>get_entity</code>, <code>graph_summary</code>, <code>get_frame</code>, <code>start_recording</code>, <code>stop_recording</code>, and more, plus <code>search</code> and <code>fetch</code> for ChatGPT connectors.</p>
    </section>
    <h2>On this computer</h2>
    <div class="connects">${groups.stdio.map(card).join("")}</div>
    <h2>Remote and APIs</h2>
    <div class="connects">${groups.http.map(card).join("")}</div>
    ${detail}
  </div>`;
}

function timelineHtml() {
  const timeline = state.timeline || { dates: [], blocks: [], frames: [], narrative: "" };
  const dates = (timeline.dates || [])
    .map(
      (date) => `<button class="chip ${date === timeline.date ? "is-on" : ""}" type="button" data-act="date" data-date="${esc(date)}">${esc(dayLabel(date))}</button>`,
    )
    .join("");
  const blocks = timeline.blocks || [];
  const range = trackRange(blocks);
  const span = Math.max(1, range.end - range.start);
  const hours = [];
  for (let minute = range.start; minute <= range.end; minute += 60) {
    const hour = Math.floor(minute / 60);
    hours.push(`<span>${hour % 12 || 12}${hour < 12 ? "a" : "p"}</span>`);
  }
  const marks = blocks
    .map((block) => {
      const left = ((minutesOfDay(block.start) - range.start) / span) * 100;
      const width = Math.max(2.2, (block.minutes / span) * 100);
      const on = state.selected === block.frame_id ? "is-on" : "";
      return `<button class="block ${on}" type="button" data-act="frame" data-id="${block.frame_id}" title="${esc(block.app_name)} ${esc(block.window_name)}" style="left:${left}%;width:${width}%;background:${color(block.app_name)}">${esc(block.app_name)}</button>`;
    })
    .join("");
  const rows = blocks
    .map(
      (block) => `<button class="row" type="button" data-act="frame" data-id="${block.frame_id}"><span class="kind">${esc(clock(block.start))}</span><span><strong>${esc(block.app_name)}</strong> · ${esc(block.window_name)}<br><span class="faint">${block.minutes} min${block.has_audio ? " · audio" : ""} · ${esc(block.text_source || "")}</span></span></button>`,
    )
    .join("");
  return `<div class="stack">
    <div class="chips">${dates}</div>
    <p class="narrative">${esc(timeline.narrative || "")}</p>
    <div class="daybar"><div class="day-line"></div>${marks}<div class="hours">${hours.join("")}</div></div>
    <div class="rows">${rows || '<p class="muted">No frames on this day.</p>'}</div>
  </div>`;
}

function trackRange(blocks) {
  if (!blocks.length) return { start: 8 * 60, end: 19 * 60 };
  const values = blocks.flatMap((block) => [minutesOfDay(block.start), minutesOfDay(block.end)]);
  const start = Math.floor(Math.min(...values) / 60) * 60;
  let end = Math.ceil(Math.max(...values) / 60) * 60;
  if (end - start < 180) end = start + 180;
  return { start, end };
}

function searchHtml() {
  const apps = [...new Set((state.timeline?.frames || []).map((frame) => frame.app_name).filter(Boolean))];
  const options = ['<option value="">Any app</option>']
    .concat(apps.map((app) => `<option ${app === state.appName ? "selected" : ""}>${esc(app)}</option>`))
    .join("");
  const types = ["all", "accessibility", "ocr", "audio", "input", "memory"]
    .map((type) => `<option value="${type}" ${type === state.contentType ? "selected" : ""}>${type}</option>`)
    .join("");
  let results = '<p class="muted">Search screen text, window titles, URLs, system events, transcripts, and memories.</p>';
  if (state.search) {
    const page = state.search.pagination || { total: 0 };
    const note = `<p class="meta">${page.total} match${page.total === 1 ? "" : "es"}.</p>`;
    const cards = (state.search.data || [])
      .map((item, index) => {
        const content = item.content || {};
        const title = content.app_name || content.speaker_name || item.type;
        const detail = content.window_name || content.event_type || "";
        return `<button class="result" type="button" data-act="result" data-index="${index}"><span class="kind">${esc(item.type)}</span><span><strong>${esc(title)}</strong> ${esc(detail)}<br><span class="snippet">${esc(snippet(content.text))}</span></span><span class="faint">${esc(clock(content.timestamp))}</span></button>`;
      })
      .join("");
    results = `${note}<div class="rows">${cards || '<p class="muted">Nothing in the stored history matches.</p>'}</div>`;
  }
  return `<div class="stack">
    <form class="searchbar" data-form="search">
      <input name="q" value="${esc(state.query)}" placeholder="invoice_id, Friday, a URL…" autocomplete="off" />
      <select name="content_type">${types}</select>
      <select name="app_name">${options}</select>
      <button class="btn primary" type="submit">Search</button>
    </form>
    ${results}
  </div>`;
}

function meetingsHtml() {
  const unnamed = state.speakers?.data || [];
  const naming = unnamed
    .map(
      (speaker) => `<form class="inline" data-form="speaker"><input type="hidden" name="id" value="${speaker.id}" /><input name="name" placeholder="Name this speaker (${speaker.segments} lines)" required /><button class="btn primary" type="submit">Save</button></form>`,
    )
    .join("");
  const meetings = (state.meetings?.data || [])
    .map((meeting) => {
      const lines = (meeting.segments || [])
        .map(
          (segment) => `<div class="line"><span class="who">${esc(segment.speaker_name || "Unnamed speaker")}</span> · <span class="faint">${esc(clock(segment.timestamp))}</span><div>${esc(segment.transcription)}</div></div>`,
        )
        .join("");
      return `<article class="meeting"><h2>${esc(meeting.title)}</h2><p class="meta">${esc(clock(meeting.started_at))} – ${esc(clock(meeting.ended_at))} · ${esc(meeting.app_name || "")}</p>${lines}</article>`;
    })
    .join("");
  return `<div class="stack">
    <p class="muted">Transcripts come from any speech-to-text tool that posts to <code>POST /add</code> with <code>content_type=audio</code>. Speakers become person nodes in the graph.</p>
    ${naming ? `<section class="card"><h2>Unnamed speakers</h2><p class="muted">Naming writes the label onto every line from that voice.</p><div class="stack">${naming}</div></section>` : ""}
    ${meetings || '<p class="muted">No meetings stored.</p>'}
  </div>`;
}

function pipesHtml() {
  const installed = (state.pipes?.data || [])
    .map((pipe) => {
      const on = Boolean(pipe.enabled);
      return `<article class="pipe"><h2>${esc(pipe.name)}</h2><p class="muted">${esc(pipe.description || "")}</p><p class="meta">${esc(pipe.schedule || "manual")} · ${on ? "scheduled" : "off"} · last run ${esc(pipe.last_run ? clock(pipe.last_run) : "never")}</p><details><summary>Prompt</summary><pre>${esc(pipe.prompt || "")}</pre></details><div class="actions"><button class="btn" type="button" data-act="pipe-toggle" data-name="${esc(pipe.name)}" data-enabled="${on ? "1" : "0"}">${on ? "Pause" : "Schedule"}</button><button class="btn primary" type="button" data-act="pipe-run" data-name="${esc(pipe.name)}">Run now</button><button class="btn" type="button" data-act="pipe-logs" data-name="${esc(pipe.name)}">Logs</button></div></article>`;
    })
    .join("");
  const discover = (state.pipes?.discover || [])
    .map(
      (pipe) => `<article class="pipe"><h2>${esc(pipe.name)}</h2><p class="muted">${esc(pipe.description)}</p><p class="meta">${esc(pipe.schedule)}</p><button class="btn" type="button" data-act="pipe-install" data-name="${esc(pipe.name)}">Install</button></article>`,
    )
    .join("");
  return `<div class="stack">
    <p class="muted">A pipe is a prompt plus a schedule. Running one writes a markdown report from local history. Pipes live as files under ~/.chronicle/pipes.</p>
    ${installed || '<p class="muted">No pipes installed.</p>'}
    ${discover ? `<h2>Library</h2>${discover}` : ""}
    <section class="card"><h2>New pipe</h2><form class="stack" data-form="pipe"><input name="name" placeholder="name-like-this" required /><input name="schedule" placeholder="daily 17:00" value="daily 17:00" /><textarea name="prompt" placeholder="What should the report say?" required></textarea><button class="btn primary" type="submit">Install</button></form></section>
  </div>`;
}

function workflowsHtml() {
  const cards = (state.workflows?.data || [])
    .map((item) => {
      const apps = (item.apps || []).join(", ");
      return `<article class="workflow"><h2>${esc(item.title)}</h2><p class="muted">${esc(item.description || "")}</p><p class="meta">${esc(apps)} · ${item.minutes || 0} min · score ${item.score}</p><p>${esc(item.automation || "")}</p><p class="snippet">${esc(item.evidence || "")}</p><button class="btn" type="button" data-act="workflow" data-id="${item.id}">Install as a pipe</button></article>`;
    })
    .join("");
  return `<div class="stack"><p class="muted">Loops that repeat in the recorded frames. Hand one to an agent with the <code>learn_from_recording</code> MCP prompt.</p>${cards || '<p class="muted">No repeated workflow yet.</p>'}</div>`;
}

function toggle(name, label, on) {
  return `<label class="check"><input type="checkbox" name="${name}" ${on ? "checked" : ""} /> ${esc(label)}</label>`;
}

function settingsHtml() {
  const s = state.settings || {};
  const ai = state.ai || {};
  return `<div class="stack">
    <section class="card">
      <h2>Capture</h2>
      <form class="stack" data-form="capture-settings">
        <div class="inline">
          <label class="field">Seconds between samples <input name="capture_interval_secs" type="number" min="1" max="600" value="${esc(s.capture_interval_secs)}" /></label>
          <label class="field">Idle after (seconds) <input name="idle_threshold_secs" type="number" min="15" value="${esc(s.idle_threshold_secs)}" /></label>
        </div>
        <div class="chips">${toggle("capture_screenshots", "Screenshots", s.capture_screenshots)}${toggle("capture_ocr", "OCR when tesseract is installed", s.capture_ocr)}${toggle("capture_clipboard", "Clipboard changes", s.capture_clipboard)}${toggle("redact", "Redact emails and secrets", s.redact)}</div>
        <div class="actions"><button class="btn primary" type="submit">Save capture settings</button></div>
      </form>
    </section>
    <section class="card">
      <h2>Retention</h2>
      <form class="inline" data-form="retention">
        <input name="days" type="number" min="0" value="${esc(s.retention_days)}" />
        <button class="btn" type="submit">Keep this many days</button>
      </form>
      <p class="faint">0 keeps everything. A shorter window deletes older frames now.</p>
    </section>
    <section class="card">
      <h2>Model for Ask and pipes</h2>
      <p class="muted">${ai.configured ? `Using ${esc(ai.model)} at ${esc(ai.base_url)}.` : "Answers come from local history. Add any OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama, LM Studio) to write them with a model."}</p>
      <form class="stack ai-form" data-form="ai">
        <input name="ai_base_url" placeholder="http://localhost:11434/v1" value="${esc(s.ai_base_url || "")}" />
        <input name="ai_model" placeholder="model name" value="${esc(s.ai_model || "")}" />
        <input name="ai_api_key" type="password" placeholder="${s.ai_api_key_set ? "Key saved — type to replace" : "API key (blank for local servers)"}" autocomplete="off" />
        <button class="btn" type="submit">Save model</button>
      </form>
    </section>
    <section class="card">
      <h2>Data</h2>
      <p class="muted">Everything lives in <code>${esc(s.home || "~/.chronicle")}</code>. Database ${bytes(state.health?.db_bytes)}. API docs at <a href="/docs">/docs</a>.</p>
      <div class="actions"><button class="btn" type="button" data-act="demo">Load sample workday</button><button class="btn danger" type="button" data-act="demo-replace">Replace history with the sample</button></div>
    </section>
  </div>`;
}

function viewHtml() {
  const views = {
    record: recordHtml,
    graph: graphHtml,
    context: contextHtml,
    connect: connectHtml,
    timeline: timelineHtml,
    search: searchHtml,
    meetings: meetingsHtml,
    pipes: pipesHtml,
    workflows: workflowsHtml,
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

function frameDrawer(bundle) {
  const frame = bundle.frame;
  const shot = frame.media_url ? `<img src="${esc(media(frame.media_url))}" alt="${esc(frame.app_name)} ${esc(frame.window_name)}" />` : "";
  const tags = (bundle.tags || []).map((tag) => `<span class="chip">${esc(tag)}</span>`).join("");
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
    <header><div><p class="kicker">${esc(stamp(frame.timestamp))} · frame ${frame.frame_id}${frame.session_id ? ` · recording ${frame.session_id}` : ""}</p><h2>${esc(frame.app_name)} · ${esc(frame.window_name)}</h2></div><button class="btn" type="button" data-act="close">Close</button></header>
    ${frame.browser_url ? `<p class="meta"><a href="${esc(frame.browser_url)}" target="_blank" rel="noreferrer">${esc(frame.browser_url)}</a></p>` : ""}
    ${shot}
    <p class="snippet">${esc(frame.text || "")}</p>
    <div class="chips">${tags}</div>
    ${events ? `<h2>System events</h2>${events}` : ""}
    ${elements ? `<h2>Elements</h2>${elements}` : ""}
    ${audio ? `<h2>Audio nearby</h2>${audio}` : ""}
    <div class="actions"><button class="btn" type="button" data-act="memory">Save as a memory</button></div>
  </aside></div>`;
}

function logsDrawer(name, payload) {
  const runs = payload.executions || [];
  const body = runs.length
    ? runs
        .map((run) => `<article class="line"><strong>${esc(run.status)}</strong> · ${esc(clock(run.started_at))}<pre>${esc(run.stdout || run.stderr || "")}</pre></article>`)
        .join("")
    : `<p class="muted">No runs yet.</p>`;
  return `<div class="backdrop" data-act="close"><aside class="sheet" data-sheet>
    <header><h2>${esc(name)}</h2><button class="btn" type="button" data-act="close">Close</button></header>
    ${body}
  </aside></div>`;
}

function textDrawer(title, text, meta) {
  return `<div class="backdrop" data-act="close"><aside class="sheet" data-sheet>
    <header><div><p class="kicker">${esc(meta || "")}</p><h2>${esc(title)}</h2></div><button class="btn" type="button" data-act="close">Close</button></header>
    <p class="snippet">${esc(text || "")}</p>
  </aside></div>`;
}

async function openFrame(id) {
  const bundle = await api(`/frames/${id}/context`);
  state.bundle = bundle;
  state.selected = Number(id);
  const drawer = $("#drawer");
  if (!drawer) return;
  drawer.innerHTML = frameDrawer(bundle);
  document.querySelectorAll(".block, .thumb").forEach((node) => {
    node.classList.toggle("is-on", node.dataset.id === String(id));
  });
}

async function buildContext() {
  const params = new URLSearchParams();
  if (state.contextTask) params.set("task", state.contextTask);
  if (state.contextScope) params.set("session_id", state.contextScope);
  if (state.contextMinutes) params.set("minutes", state.contextMinutes);
  state.context = await api(`/context?${params.toString()}`);
}

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
  row.className = "chips";
  for (const cite of citations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
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
    await go("graph");
    return;
  }
  if (start === "record") {
    await api("/sessions", { method: "POST", body: JSON.stringify({ duration: "15m", label: "First recording" }) });
    toast("Recording for 15 minutes");
    await go("record");
    return;
  }
  await go("connect");
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
  async date(button) {
    state.date = button.dataset.date;
    state.timeline = await api(`/timeline?date=${encodeURIComponent(state.date)}`);
    paint();
  },
  async frame(button) {
    await openFrame(button.dataset.id);
  },
  close(button, event) {
    if (event?.target?.closest("[data-sheet]") && !event.target.closest("button")) return;
    closeDrawer();
  },
  async prompt(button) {
    const input = document.querySelector("[name=question]");
    if (input) input.value = button.dataset.q;
    await streamAsk(button.dataset.q);
  },
  async citation(button) {
    const type = button.dataset.type;
    const frameId = button.dataset.frame || (["frame", "accessibility", "ocr"].includes(type) ? button.dataset.id : "");
    if (frameId) {
      await openFrame(frameId);
      return;
    }
    if (type === "audio") await go("meetings");
    else if (type === "workflow") await go("workflows");
  },
  async result(button) {
    const item = state.search?.data?.[Number(button.dataset.index)];
    if (!item) return;
    const content = item.content || {};
    if (content.frame_id && item.type !== "Input") {
      await openFrame(content.frame_id);
      return;
    }
    const drawer = $("#drawer");
    if (drawer) drawer.innerHTML = textDrawer(content.app_name || content.speaker_name || item.type, content.text, clock(content.timestamp));
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
  async "session-graph"(button) {
    await go("graph", { graphScope: button.dataset.id, node: null, graphResults: null, graphKind: "" });
  },
  async "session-context"(button) {
    state.contextScope = button.dataset.id;
    state.contextMinutes = "";
    await buildContext();
    await go("context");
  },
  async node(button) {
    const scope = scopeParam(state.graphScope);
    state.node = await api(`/graph/node?id=${encodeURIComponent(button.dataset.id)}${scope ? `&${scope}` : ""}`);
    paintView();
  },
  async "node-context"(button) {
    state.contextTask = button.dataset.label;
    state.contextScope = state.graphScope;
    await buildContext();
    await go("context");
  },
  async "graph-kind"(button) {
    state.graphKind = button.dataset.kind;
    paintView();
  },
  async "graph-build"(button) {
    button.disabled = true;
    const built = await api("/graph/build", {
      method: "POST",
      body: JSON.stringify({ session_id: state.graphScope ? Number(state.graphScope) : null }),
    });
    await loadGraph();
    state.node = null;
    paintView();
    toast(`Built ${built.nodes} nodes and ${built.edges} edges`);
  },
  "copy-context"() {
    if (state.context) copy(state.context.markdown);
  },
  async client(button) {
    state.client = await api(`/clients/${encodeURIComponent(button.dataset.id)}`);
    paintView();
  },
  "copy-snippet"() {
    if (state.client) copy(state.client.snippet);
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
    state.date = "";
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
    state.date = "";
    state.search = null;
    state.node = null;
    state.graphScope = "";
    await loadView(state.view);
    paint();
    toast("History replaced with the sample workday");
  },
  async "pipe-run"(button) {
    button.disabled = true;
    const result = await api(`/pipes/${encodeURIComponent(button.dataset.name)}/run`, { method: "POST" });
    state.pipes = await api("/pipes");
    paint();
    const drawer = $("#drawer");
    if (drawer) drawer.innerHTML = logsDrawer(button.dataset.name, { executions: [result] });
    toast(result.status === "success" ? "Pipe finished" : "Pipe failed");
  },
  async "pipe-toggle"(button) {
    const enabled = button.dataset.enabled !== "1";
    await api(`/pipes/${encodeURIComponent(button.dataset.name)}/enable`, { method: "POST", body: JSON.stringify({ enabled }) });
    state.pipes = await api("/pipes");
    paint();
  },
  async "pipe-install"(button) {
    await api("/pipes/install", { method: "POST", body: JSON.stringify({ source: `catalog:${button.dataset.name}` }) });
    state.pipes = await api("/pipes");
    paint();
    toast("Installed, still off");
  },
  async "pipe-logs"(button) {
    const logs = await api(`/pipes/${encodeURIComponent(button.dataset.name)}/logs`);
    const drawer = $("#drawer");
    if (drawer) drawer.innerHTML = logsDrawer(button.dataset.name, logs);
  },
  async workflow(button) {
    const created = await api(`/workflows/${button.dataset.id}/install-pipe`, { method: "POST" });
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
  async graph(form) {
    const data = new FormData(form);
    const scope = String(data.get("scope") || "");
    state.graphQuery = String(data.get("q") || "");
    if (scope !== state.graphScope) {
      state.graphScope = scope;
      state.node = null;
      await loadGraph();
    }
    const params = new URLSearchParams({ limit: "12" });
    if (state.graphQuery) params.set("q", state.graphQuery);
    if (scope) params.set("session_id", scope);
    state.graphResults = state.graphQuery ? (await api(`/graph/search?${params.toString()}`)).data : null;
    paintView();
  },
  async context(form) {
    const data = new FormData(form);
    state.contextTask = String(data.get("task") || "");
    state.contextScope = String(data.get("scope") || "");
    state.contextMinutes = String(data.get("minutes") || "");
    await buildContext();
    paintView();
  },
  async search(form) {
    const data = new FormData(form);
    state.query = String(data.get("q") || "");
    state.contentType = String(data.get("content_type") || "all");
    state.appName = String(data.get("app_name") || "");
    const params = new URLSearchParams({ content_type: state.contentType, limit: "30" });
    if (state.query) params.set("q", state.query);
    if (state.appName) params.set("app_name", state.appName);
    state.search = await api(`/search?${params.toString()}`);
    paint();
  },
  async speaker(form) {
    const data = new FormData(form);
    await api("/speakers/update", {
      method: "POST",
      body: JSON.stringify({ id: Number(data.get("id")), name: String(data.get("name") || "") }),
    });
    state.speakers = await api("/speakers/unnamed");
    state.meetings = await api("/meetings");
    paint();
    toast("Speaker named");
  },
  async pipe(form) {
    const data = new FormData(form);
    await api("/pipes/install", {
      method: "POST",
      body: JSON.stringify({
        name: String(data.get("name") || ""),
        schedule: String(data.get("schedule") || "manual"),
        prompt: String(data.get("prompt") || ""),
      }),
    });
    state.pipes = await api("/pipes");
    paint();
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

document.addEventListener("click", async (event) => {
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
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

async function boot() {
  paint();
  try {
    state.settings = await api("/settings");
    state.health = await api("/health");
    if (state.settings.onboarded) {
      const hash = location.hash.replace("#", "");
      if (VIEWS.includes(hash)) state.view = hash;
      await loadView(state.view);
    }
  } catch (error) {
    state.bootError = error.message || "The local engine is not reachable.";
  }
  state.ready = true;
  paint();
  startPolling();
}

boot();
