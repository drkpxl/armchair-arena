const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) n.append(k);
  return n;
};

const selected = new Set();

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
      });
      label.addEventListener("mouseenter", (e) => showTip(m, e));
      label.addEventListener("mousemove", (e) => { if (tip.classList.contains("show")) positionTip(e); });
      label.addEventListener("mouseleave", hideTip);
      box.append(label);
    }
    selectRandom(3);
  } catch (e) {
    box.textContent = "Failed to load models: " + e;
  }
}

function metric(label, value) {
  return el("span", { className: "metric" }, el("b", { textContent: value }), document.createTextNode(" " + label));
}

function stars(runId) {
  const wrap = el("div", { className: "stars" });
  const saved = el("span", { className: "saved" });
  let current = 0;
  const cells = [];
  for (let i = 1; i <= 5; i++) {
    const s = el("span", { textContent: "★" });
    const paint = (n) => cells.forEach((c, idx) => c.classList.toggle("on", idx < n));
    s.addEventListener("mouseenter", () => paint(i));
    s.addEventListener("mouseleave", () => paint(current));
    s.addEventListener("click", async () => {
      current = i; paint(i); saved.textContent = "saving…";
      try {
        const r = await fetch("/api/vote", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_id: runId, rating: i }),
        });
        saved.textContent = r.ok ? "✓ saved" : "save failed";
      } catch { saved.textContent = "save failed"; }
    });
    cells.push(s); wrap.append(s);
  }
  wrap.append(saved);
  return wrap;
}

function sourcesBlock(sources) {
  const wrap = el("div", { className: "sources" });
  wrap.append(el("div", { className: "sources-title", textContent: `Sources (${sources.length}) — credible?` }));
  for (const s of sources) {
    const row = el("div", { className: "source-row" });
    const yes = el("button", { className: "src-btn yes", textContent: "✓", title: "credible" });
    const no = el("button", { className: "src-btn no", textContent: "✗", title: "not credible" });
    const paint = (c) => { yes.classList.toggle("on", c === true); no.classList.toggle("on", c === false); };
    const vote = async (credible) => {
      paint(credible);
      try {
        await fetch("/api/source_vote", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_id: s.id, credible }),
        });
      } catch {}
    };
    yes.addEventListener("click", () => vote(true));
    no.addEventListener("click", () => vote(false));
    const link = el("a", { href: s.url, target: "_blank", rel: "noopener", textContent: s.domain || s.url, title: s.url });
    const role = el("span", { className: "src-role", textContent: s.role === "scraped" ? "scraped" : "search" });
    row.append(yes, no, link, role);
    if (s.credible === 1) paint(true); else if (s.credible === 0) paint(false);
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

  c.append(stars(res.id));
  return c;
}

$("#run").addEventListener("click", async () => {
  const question = $("#question").value.trim();
  if (!question) { $("#status").textContent = "Enter a question."; return; }
  if (!selected.size) { $("#status").textContent = "Select at least one model."; return; }

  const btn = $("#run");
  btn.disabled = true;
  $("#status").textContent = `Running ${selected.size} model(s)… (cloud models can take a while)`;
  $("#cards").textContent = "";
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, models: [...selected] }),
    });
    if (!r.ok) throw new Error(await r.text());
    const { results } = await r.json();
    $("#status").textContent = "Done. Vote each answer 1–5.";
    for (const res of results) $("#cards").append(card(res));
  } catch (e) {
    $("#status").textContent = "Failed: " + e;
  } finally {
    btn.disabled = false;
  }
});

$("#reset").addEventListener("click", () => {
  $("#question").value = "";
  $("#cards").textContent = "";
  $("#status").textContent = "";
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
