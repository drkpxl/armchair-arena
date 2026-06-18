// Model Manager: the onboarding screen on first run, and the edit-anytime roster editor
// (opened from the ⚙ Models nav action). Self-contained (own helpers in an IIFE) so it
// shares no globals with app.js. It persists the roster + backends server-side via
// /api/roster, which is what makes selections survive across sessions and browsers.
(function () {
  const q = (s) => document.querySelector(s);
  const ce = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    for (const k of kids) n.append(k);
    return n;
  };
  const genId = () => "b" + Math.random().toString(36).slice(2, 10);

  // Every run pits exactly 3 models, so a usable roster needs at least 3.
  const MIN_ROSTER = 3;

  let overlay = null;   // root .modal-overlay element (built once, reused)
  let body = null;      // the .modal-card element whose contents we re-render
  let state = null;     // working copy: { backends:[...], selected:Map(model->backend_id), onboarding }

  function claimedElsewhere(name, backendId) {
    return state.selected.has(name) && state.selected.get(name) !== backendId;
  }

  // ---- data ----
  async function probeBackend(b) {
    b.loaded = false; b.error = null;
    render();
    try {
      // Saved backends probe by id (server resolves host/key); a just-added server probes
      // by its raw host + key (it isn't persisted yet).
      const payload = b.persisted ? { backend_id: b.id } : { host: b.host, api_key: b.api_key || "" };
      const data = await (await fetch("/api/probe", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })).json();
      b.models = data.models || [];
      b.error = data.error || null;
    } catch (e) {
      b.models = []; b.error = String(e);
    }
    b.loaded = true;
    render();
  }

  function toggleModel(name, backendId, checked) {
    if (checked) state.selected.set(name, backendId);
    else if (state.selected.get(name) === backendId) state.selected.delete(name);
    render();
  }

  function removeBackend(b) {
    state.backends = state.backends.filter((x) => x !== b);
    // Drop any roster entries that pointed at this backend.
    for (const [name, bid] of [...state.selected]) if (bid === b.id) state.selected.delete(name);
    render();
  }

  async function save(errBox, saveBtn) {
    const backends = state.backends
      .filter((b) => !b.builtin)
      .map((b) => {
        const out = { id: b.id, label: b.label, host: b.host };
        if (b.api_key) out.api_key = b.api_key;  // omit ⇒ server preserves the stored key
        return out;
      });
    const roster = [...state.selected].map(([model, backend_id]) => ({ model, backend_id }));
    saveBtn.disabled = true; saveBtn.textContent = "Saving…";
    try {
      const r = await fetch("/api/roster", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backends, roster }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || ("save failed (" + r.status + ")"));
      }
      close();
      window.dispatchEvent(new Event("roster-saved"));
    } catch (e) {
      errBox.textContent = String(e.message || e);
      saveBtn.disabled = false; saveBtn.textContent = "Save";
    }
  }

  // ---- rendering ----
  function modelPill(b, name) {
    const claimed = claimedElsewhere(name, b.id);
    const checked = state.selected.get(name) === b.id;
    const input = ce("input", { type: "checkbox", checked, disabled: claimed });
    const label = ce("label", {}, input, document.createTextNode(name));
    if (checked) label.classList.add("checked");
    if (claimed) { label.classList.add("disabled"); label.title = "Already added from another backend"; }
    input.addEventListener("change", () => toggleModel(name, b.id, input.checked));
    return label;
  }

  function backendBlock(b) {
    const block = ce("div", { className: "backend-block" });
    const head = ce("div", { className: "backend-head" },
      ce("span", { className: "backend-title", textContent: b.label }),
      ce("span", { className: "backend-host", textContent: b.host || "" }));
    if (!b.builtin) {
      const rm = ce("button", { type: "button", className: "secondary backend-remove", textContent: "✕ remove" });
      rm.addEventListener("click", () => removeBackend(b));
      head.append(rm);
    }
    block.append(head);

    const list = ce("div", { className: "models" });
    if (!b.loaded) {
      list.append(ce("span", { className: "hint", textContent: "loading models…" }));
    } else if (b.error) {
      list.append(ce("span", { className: "hint", textContent: "Couldn't reach this backend: " + b.error }));
    } else if (!b.models.length) {
      list.append(ce("span", { className: "hint", textContent: "No models within the freshness window." }));
    } else {
      for (const name of b.models) list.append(modelPill(b, name));
    }
    block.append(list);
    return block;
  }

  function addServerForm() {
    const wrap = ce("div", { className: "add-server" });
    const label = ce("input", { type: "text", placeholder: "Label (e.g. Tailscale box)" });
    const host = ce("input", { type: "text", placeholder: "http://host:11434" });
    const key = ce("input", { type: "password", placeholder: "API key (optional)" });
    const connect = ce("button", { type: "button", className: "secondary", textContent: "Connect" });
    const err = ce("span", { className: "modal-err" });
    connect.addEventListener("click", async () => {
      const url = host.value.trim();
      if (!url) { err.textContent = "Enter a server URL."; return; }
      err.textContent = ""; connect.disabled = true; connect.textContent = "Connecting…";
      try {
        const data = await (await fetch("/api/probe", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host: url, api_key: key.value.trim() }),
        })).json();
        if (data.error) throw new Error(data.error);
        state.backends.push({
          id: genId(), label: label.value.trim() || url, host: url,
          api_key: key.value.trim(), builtin: false, persisted: false,
          models: data.models || [], loaded: true, error: null,
        });
        render();
      } catch (e) {
        err.textContent = "Connect failed: " + (e.message || e);
        connect.disabled = false; connect.textContent = "Connect";
      }
    });
    wrap.append(label, host, key, connect);
    const block = ce("div", {},
      ce("div", { className: "backend-title", textContent: "Add an Ollama server", style: "margin-bottom:8px" }),
      wrap, err);
    return block;
  }

  function render() {
    if (!body) return;
    body.textContent = "";
    body.append(ce("h2", { textContent: state.onboarding ? "Welcome — pick your models" : "Manage models" }));
    body.append(ce("div", {
      className: "modal-intro",
      textContent: state.onboarding
        ? "Choose which models compete in the arena. Add a local/remote Ollama server below to include its models. You can change this anytime under ⚙ Models."
        : "Add or remove models and backends. Changes persist across sessions and browsers.",
    }));

    for (const b of state.backends) body.append(backendBlock(b));
    body.append(addServerForm());

    const n = state.selected.size;
    const err = ce("div", { className: "modal-err" });
    const saveBtn = ce("button", { type: "button", textContent: "Save", disabled: n < MIN_ROSTER });
    saveBtn.addEventListener("click", () => save(err, saveBtn));
    const cancel = ce("button", { type: "button", className: "secondary", textContent: "Cancel" });
    cancel.addEventListener("click", close);
    const footer = ce("div", { className: "modal-footer" },
      ce("span", { className: "count", textContent: `${n} selected${n < MIN_ROSTER ? ` — pick at least ${MIN_ROSTER}` : ""}` }),
      saveBtn, cancel);
    body.append(err, footer);
  }

  function close() {
    if (overlay) { overlay.remove(); overlay = null; body = null; }
  }

  async function open({ onboarding = false } = {}) {
    let data;
    try {
      data = await (await fetch("/api/roster")).json();
    } catch (e) {
      alert("Couldn't load model config: " + e);
      return;
    }
    state = {
      backends: data.backends.map((b) => ({
        ...b, persisted: true, models: [], loaded: false, error: null, api_key: "",
      })),
      selected: new Map(data.roster.map((r) => [r.model, r.backend_id])),
      onboarding,
    };
    overlay = ce("div", { className: "modal-overlay" });
    body = ce("div", { className: "modal-card" });
    overlay.append(body);
    // Click the dark backdrop (not the card) to dismiss.
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.body.append(overlay);
    render();
    state.backends.forEach(probeBackend);
  }

  window.openManager = open;

  // Wire the nav action and trigger first-run onboarding (empty roster ⇒ never onboarded).
  const btn = q("#manageBtn");
  if (btn) btn.addEventListener("click", (e) => { e.preventDefault(); open({ onboarding: false }); });
  (async () => {
    try {
      const data = await (await fetch("/api/roster")).json();
      if (!data.onboarded) open({ onboarding: true });
    } catch { /* health banner will surface backend issues */ }
  })();
})();
