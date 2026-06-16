const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) n.append(k);
  return n;
};

const selected = new Set();

// Every run pits exactly three models against each other — that's what makes the
// winner picks comparable and the analytics quantifiable.
const MAX_MODELS = 3;

// Reflect the "exactly 3" rule in the UI: once 3 are checked, grey out the rest so a
// 4th can't be added, and only enable Run at exactly 3.
function syncPickerLimits() {
  const atMax = selected.size >= MAX_MODELS;
  for (const input of document.querySelectorAll("#models input")) {
    input.disabled = atMax && !input.checked;
    input.closest("label").classList.toggle("disabled", input.disabled);
  }
  const run = $("#run");
  if (run) run.disabled = selected.size !== MAX_MODELS || polling;
}

// Pre-select n random models (used on load and on reset, so a lazy run always
// pits a fresh trio against each other).
function selectRandom(n) {
  const inputs = [...document.querySelectorAll("#models input")];
  inputs.forEach((i) => { i.checked = false; i.closest("label").classList.remove("checked"); });
  selected.clear();
  for (let i = inputs.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [inputs[i], inputs[j]] = [inputs[j], inputs[i]];
  }
  for (const input of inputs.slice(0, n)) {
    input.checked = true;
    input.closest("label").classList.add("checked");
    selected.add(input.value);
  }
  syncPickerLimits();
}

// ---- model info tooltip (lazy-fetched, cached) ----
const infoCache = {};
const tip = el("div", { id: "tooltip" });
document.body.append(tip);

function renderTip(info, loading) {
  tip.innerHTML = "";
  tip.append(el("div", { className: "tt-name", textContent: info.name }));
  if (loading) { tip.append(el("div", { className: "tt-row", textContent: "loading…" })); return; }
  if (info.error) { tip.append(el("div", { className: "tt-row", textContent: info.error })); return; }
  const row = (label, val) => {
    if (val) tip.append(el("div", { className: "tt-row" }, el("b", { textContent: val }), document.createTextNode(" " + label)));
  };
  row("parameters", info.parameter_size);
  row("architecture", info.architecture);
  row("context window", info.context);
  row("quantization", info.quantization);
  row("updated", info.modified_at);
  if (info.capabilities && info.capabilities.length) {
    const caps = el("div", { className: "tt-caps" });
    for (const c of info.capabilities) caps.append(el("span", { className: "tt-cap", textContent: c }));
    tip.append(caps);
  }
}

function positionTip(e) {
  const pad = 14, r = tip.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + r.width > window.innerWidth) x = e.clientX - r.width - pad;
  if (y + r.height > window.innerHeight) y = e.clientY - r.height - pad;
  tip.style.left = Math.max(4, x) + "px";
  tip.style.top = Math.max(4, y) + "px";
}

async function showTip(model, e) {
  tip.classList.add("show");
  if (infoCache[model]) {
    renderTip(infoCache[model]);
  } else {
    renderTip({ name: model }, true);
    positionTip(e);
    try {
      infoCache[model] = await (await fetch("/api/model_info?name=" + encodeURIComponent(model))).json();
    } catch (err) {
      infoCache[model] = { name: model, error: String(err) };
    }
    renderTip(infoCache[model]);
  }
  positionTip(e);
}

function hideTip() { tip.classList.remove("show"); }

async function loadModels() {
  const box = $("#models");
  box.textContent = "loading models…";
  try {
    const { models, error } = await (await fetch("/api/models")).json();
    box.textContent = "";
    if (error) {
      box.append(el("span", { className: "hint", textContent: "Error loading models: " + error }));
      return;
    }
    if (!models.length) {
      box.append(el("span", { className: "hint", textContent: "No models found." }));
      return;
    }
    for (const m of models) {
      const id = "m_" + m;
      const input = el("input", { type: "checkbox", value: m, id });
      const label = el("label", { htmlFor: id }, input, document.createTextNode(m));
      input.addEventListener("change", () => {
        if (input.checked) { selected.add(m); label.classList.add("checked"); }
        else { selected.delete(m); label.classList.remove("checked"); }
        syncPickerLimits();
      });
      label.addEventListener("mouseenter", (e) => showTip(m, e));
      label.addEventListener("mousemove", (e) => { if (tip.classList.contains("show")) positionTip(e); });
      label.addEventListener("mouseleave", hideTip);
      box.append(label);
    }
    selectRandom(3);
    if (models.length < MAX_MODELS) {
      // Run gates on exactly 3 selected, so too few models would silently disable it
      // with no explanation — say why instead.
      $("#status").textContent =
        `Only ${models.length} model${models.length === 1 ? "" : "s"} available — need ${MAX_MODELS} to run. Check the model backend / EXCLUDE_MODELS.`;
    }
  } catch (e) {
    box.textContent = "Failed to load models: " + e;
  }
}

// Collapse the (tall) model picker after a run so results aren't pushed off-screen on
// mobile; tapping the summary bar — or Reset / a reload — brings it back.
function setModelsCollapsed(collapsed) {
  $("#modelsHint").hidden = collapsed;
  $("#models").hidden = collapsed;
  const bar = $("#modelsBar");
  bar.hidden = !collapsed;
  if (collapsed) bar.textContent = `▸ ${selected.size} model${selected.size === 1 ? "" : "s"} selected — tap to edit`;
}

$("#modelsBar").addEventListener("click", () => setModelsCollapsed(false));

function metric(label, value) {
  return el("span", { className: "metric" }, el("b", { textContent: value }), document.createTextNode(" " + label));
}

// Pick the single best answer of the trio. One winner per batch: clicking another
// card moves the crown; clicking the current winner again clears it (batch becomes
// undecided and drops out of the analytics). The server enforces the one-per-batch
// rule; `winnerRunId` (module-level) is what keeps the highlight correct when a card
// is rebuilt by polling — the poll payload always carries win=0, so without it a
// re-render would silently drop the crown the user already picked.
function winnerButton(res) {
  const wrap = el("div", { className: "winner-wrap" });
  // A failed/empty answer can't be "the best", and an unsaved run has no id to vote on.
  const pickable = res.id != null && res.error == null;
  const isWinner = pickable && res.id === winnerRunId;
  const btn = el("button", {
    className: isWinner ? "winner-btn on" : "winner-btn", type: "button",
    textContent: isWinner ? "★ Winner" : "Pick as winner", disabled: !pickable,
  });
  const saved = el("span", { className: "saved" });
  btn.addEventListener("click", async () => {
    const nowWin = !btn.classList.contains("on");
    for (const b of document.querySelectorAll("#cards .winner-btn.on")) {
      b.classList.remove("on"); b.textContent = "Pick as winner";
      const sib = b.parentElement.querySelector(".saved");
      if (sib) sib.textContent = "";  // drop a stale "✓ saved" on the dethroned card
    }
    winnerRunId = nowWin ? res.id : null;
    if (nowWin) { btn.classList.add("on"); btn.textContent = "★ Winner"; }
    saved.textContent = "saving…";
    try {
      const r = await fetch("/api/winner", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: res.id, win: nowWin }),
      });
      saved.textContent = r.ok ? "✓ saved" : "save failed";
    } catch { saved.textContent = "save failed"; }
  });
  wrap.append(btn, saved);
  return wrap;
}

// The web sources a model touched. Shown for context (which pages it read) — there's
// no voting; quality is judged by picking the winning answer, not rating each URL.
function sourcesBlock(sources) {
  const wrap = el("div", { className: "sources" });
  wrap.append(el("div", { className: "sources-title", textContent: `Sources (${sources.length})` }));
  for (const s of sources) {
    const row = el("div", { className: "source-row" });
    const link = el("a", { href: s.url, target: "_blank", rel: "noopener", textContent: s.domain || s.url, title: s.url });
    const role = el("span", { className: "src-role", textContent: s.role === "scraped" ? "scraped" : "search" });
    row.append(link, role);
    wrap.append(row);
  }
  return wrap;
}

function card(res) {
  const c = el("div", { className: "card" });
  c.append(el("h3", {}, el("span", { className: "model-name", textContent: res.model })));

  const metrics = el("div", { className: "metrics" });
  if (res.error) {
    metrics.append(el("span", { className: "metric error", textContent: "error" }));
  } else {
    metrics.append(metric("tokens", res.total_tokens));
    metrics.append(metric("→ out", res.completion_tokens));
    metrics.append(metric("tok/s", res.tokens_per_sec ?? "—"));
    metrics.append(metric("wall", (res.wall_clock_ms / 1000).toFixed(1) + "s"));
    metrics.append(metric("tools", res.tool_calls));
  }
  c.append(metrics);

  if (res.error) {
    c.append(el("div", { className: "answer error", textContent: res.error }));
  } else {
    const ans = el("div", { className: "answer markdown" });
    ans.innerHTML = marked.parse(res.answer || "_(empty answer)_");
    c.append(ans);
  }

  if (res.sources && res.sources.length) c.append(sourcesBlock(res.sources));

  const trace = JSON.parse(res.tool_trace || "[]");
  if (trace.length) {
    const d = el("details");
    d.append(el("summary", { textContent: `tool trace (${trace.length})` }));
    d.append(el("pre", { textContent: JSON.stringify(trace, null, 2) }));
    c.append(d);
  }

  c.append(winnerButton(res));
  return c;
}

// Placeholder card shown while a model is still running, replaced in place when its
// result arrives. Keeps a model's slot visible so progress is legible.
function pendingCard(model) {
  const c = el("div", { className: "card pending" });
  c.append(el("h3", {}, el("span", { className: "model-name", textContent: model })));
  c.append(el("div", { className: "spinner", textContent: "running…" }));
  return c;
}

// ---- batch polling ----
// A batch runs server-side, decoupled from any single request, so leaving the tab or
// switching apps never wastes the query: the work keeps going and each poll recovers
// whatever has finished. The active batch id is stashed in localStorage so even a full
// reload (or a phone killing the page) resumes instead of losing the run.
const ACTIVE_KEY = "armchair.activeBatch";
let polling = false;
let winnerRunId = null;  // run id the user picked as winner this batch (survives card re-renders)

// Run stays disabled while a batch is in flight AND whenever the picker isn't at
// exactly 3, so finishing a run doesn't silently re-enable an invalid selection.
function setRunning(on) { $("#run").disabled = on || selected.size !== MAX_MODELS; }

async function pollBatch(batchId, models, rendered) {
  // rendered: Map(model -> card element). Pre-seed placeholders for any not yet drawn.
  for (const m of models) {
    if (!rendered.has(m)) {
      const ph = pendingCard(m);
      rendered.set(m, ph);
      $("#cards").append(ph);
    }
  }
  polling = true;
  setRunning(true);
  while (polling) {
    let state;
    try {
      const r = await fetch("/api/batch/" + encodeURIComponent(batchId));
      if (r.status === 404) {  // server restarted or batch pruned
        $("#status").textContent = "This run is no longer available (server restarted). Run again.";
        localStorage.removeItem(ACTIVE_KEY);
        break;
      }
      if (!r.ok) throw new Error(await r.text());
      state = await r.json();
    } catch (e) {
      $("#status").textContent = "Connection lost, retrying…";
      await new Promise((res) => setTimeout(res, 2000));
      continue;
    }

    for (const res of state.results) {
      const ph = rendered.get(res.model);
      const real = card(res);
      if (ph) ph.replaceWith(real); else $("#cards").append(real);
      rendered.set(res.model, real);
    }

    const doneCount = state.results.length, total = state.models.length;
    if (state.done) {
      $("#status").textContent = state.error
        ? "Finished with errors: " + state.error
        : "Done. Pick the best answer.";
      localStorage.removeItem(ACTIVE_KEY);
      break;
    }
    $("#status").textContent = `Running… ${doneCount}/${total} done (cloud models can take a while).`;
    await new Promise((res) => setTimeout(res, 2000));
  }
  polling = false;
  setRunning(false);
}

$("#run").addEventListener("click", async () => {
  const question = $("#question").value.trim();
  if (!question) { $("#status").textContent = "Enter a question."; return; }
  if (selected.size !== MAX_MODELS) { $("#status").textContent = `Select exactly ${MAX_MODELS} models.`; return; }

  const models = [...selected];
  setRunning(true);
  $("#status").textContent = `Starting ${models.length} model(s)…`;
  $("#cards").textContent = "";
  winnerRunId = null;  // fresh batch, nothing picked yet
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, models }),
    });
    if (!r.ok) throw new Error(await r.text());
    const { batch_id } = await r.json();
    localStorage.setItem(ACTIVE_KEY, JSON.stringify({ batch_id, models }));
    setModelsCollapsed(true);
    pollBatch(batch_id, models, new Map());
  } catch (e) {
    $("#status").textContent = "Failed to start: " + e;
    setRunning(false);
  }
});

// Returning to a backgrounded tab: poke an immediate poll instead of waiting out the
// interval (mobile browsers freeze timers while hidden).
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !polling) resumeActiveBatch();
});

function resumeActiveBatch() {
  const raw = localStorage.getItem(ACTIVE_KEY);
  if (!raw) return;
  let saved;
  try { saved = JSON.parse(raw); } catch { localStorage.removeItem(ACTIVE_KEY); return; }
  if (!saved.batch_id) return;
  // Re-attach to existing cards by model name so we don't duplicate them.
  const rendered = new Map();
  for (const c of $("#cards").querySelectorAll(".card")) {
    const name = c.querySelector(".model-name");
    if (name) rendered.set(name.textContent, c);
  }
  pollBatch(saved.batch_id, saved.models || [], rendered);
}

$("#shuffle").addEventListener("click", () => {
  selectRandom(MAX_MODELS);
  setModelsCollapsed(false);  // reveal the new trio so the user sees what changed
});

$("#reset").addEventListener("click", () => {
  polling = false;  // stop any in-flight poll loop so it can't re-append cards
  winnerRunId = null;
  localStorage.removeItem(ACTIVE_KEY);
  $("#question").value = "";
  $("#cards").textContent = "";
  $("#status").textContent = "";
  setRunning(false);
  setModelsCollapsed(false);
  hideTip();
  selectRandom(3);
  $("#question").focus();
});

async function checkHealth() {
  const box = $("#health");
  try {
    const h = await (await fetch("/api/health")).json();
    if (!h.ollama.ok) {
      box.className = "health bad";
      box.textContent = `⚠ Can't reach Ollama at ${h.ollama.host}: ${h.ollama.error}. Check OLLAMA_HOST / OLLAMA_API_KEY in .env.`;
      box.hidden = false;
    } else if (!h.web_tools) {
      const fc = h.firecrawl || {};
      box.className = "health warn";
      box.textContent = `ℹ Web research tools are off — ${fc.note || "search unavailable"}${fc.url ? " (" + fc.url + ")" : ""}. Models will answer from their own knowledge. See README to enable Firecrawl.`;
      box.hidden = false;
    }
  } catch { /* health is best-effort */ }
}

checkHealth();
loadModels();
resumeActiveBatch();  // recover an in-flight comparison after a reload / phone killing the page
