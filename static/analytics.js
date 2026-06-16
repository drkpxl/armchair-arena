const GRID = "#2a313d";
const TXT = "#9aa4b2";
Chart.defaults.color = TXT;
Chart.defaults.borderColor = GRID;

const palette = ["#6ea8fe", "#4ade80", "#fbbf24", "#f87171", "#c084fc", "#22d3ee", "#fb923c", "#a3e635"];

function barChart(canvasId, labels, data, { ascending = false } = {}) {
  return new Chart(document.getElementById(canvasId), {
    type: "bar",
    data: {
      labels,
      datasets: [{ data, backgroundColor: labels.map((_, i) => palette[i % palette.length]) }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true } },
    },
  });
}

function rank(rows, key, ascending) {
  return [...rows]
    .filter((r) => r[key] != null)
    .sort((a, b) => (ascending ? a[key] - b[key] : b[key] - a[key]));
}

// ---- efficiency frontier (Pareto) over quality, token cost, speed ----
// Quality = opponent-aware strength (Elo). A model is on the frontier if no other model
// beats it on strength (↑), tokens (↓), AND tokens/sec (↑) at once. Low-data models are
// excluded so a small-sample streak can't claim a non-dominated tradeoff.
function paretoFrontier(models) {
  const elig = models.filter(
    (m) => !m.low_data && m.elo != null && m.avg_total_tokens != null && m.avg_tokens_per_sec != null,
  );
  const dominates = (a, b) =>
    a.elo >= b.elo &&
    a.avg_total_tokens <= b.avg_total_tokens &&
    a.avg_tokens_per_sec >= b.avg_tokens_per_sec &&
    (a.elo > b.elo ||
      a.avg_total_tokens < b.avg_total_tokens ||
      a.avg_tokens_per_sec > b.avg_tokens_per_sec);
  return new Set(
    elig.filter((m) => !elig.some((o) => o !== m && dominates(o, m))).map((m) => m.model),
  );
}

function renderFrontierNote(frontier) {
  const f = [...frontier], el = document.getElementById("frontier-note");
  if (!f.length) {
    el.innerHTML = "Pick winners across at least a few batches per model to compute strength and the efficiency frontier.";
    return;
  }
  el.innerHTML =
    `<b>${f.length} model${f.length > 1 ? "s" : ""} on the efficiency frontier</b> — no other ` +
    `model beats ${f.length > 1 ? "them" : "it"} on strength, token cost, and speed together. ` +
    `Start here: ` + f.map((m) => `<span class="chip">⭐ ${m}</span>`).join(" ");
}

// ---- per-model summary table (sortable) ----
const MODEL_COLS = [
  ["model", "model", false], ["elo", "strength", true], ["win_rate", "win%", true],
  ["ci", "95% CI", false], ["wins", "wins", true], ["decided", "decided", true],
  ["n", "runs", true], ["avg_total_tokens", "avg tokens", true],
  ["avg_tokens_per_sec", "tok/s", true], ["wall_s", "wall (s)", true],
];
let modelRows = [], mSortKey = "elo", mSortAsc = false, frontierSet = new Set();

function renderModelsTable() {
  const thead = document.querySelector("#models-table thead");
  const tbody = document.querySelector("#models-table tbody");
  thead.innerHTML = ""; tbody.innerHTML = "";
  const htr = document.createElement("tr");
  for (const [key, label, num] of MODEL_COLS) {
    const th = document.createElement("th");
    th.textContent = label + (mSortKey === key ? (mSortAsc ? " ▲" : " ▼") : "");
    if (num) th.className = "num";
    th.onclick = () => { if (mSortKey === key) mSortAsc = !mSortAsc; else { mSortKey = key; mSortAsc = false; } renderModelsTable(); };
    htr.append(th);
  }
  thead.append(htr);
  const sorted = [...modelRows].sort((a, b) => {
    const x = a[mSortKey], y = b[mSortKey];
    if (x == null) return 1; if (y == null) return -1;
    const cmp = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
    return mSortAsc ? cmp : -cmp;
  });
  for (const m of sorted) {
    const tr = document.createElement("tr");
    const onF = frontierSet.has(m.model);
    if (onF) tr.className = "frontier";
    if (m.low_data) tr.classList.add("low-data");
    for (const [key, , num] of MODEL_COLS) {
      const td = document.createElement("td");
      let v = m[key];
      if (key === "model") v = (onF ? "⭐ " : "") + m.model + (m.low_data ? " ⚠" : "");
      else if (key === "elo") v = v == null ? "—" : v;
      else if (key === "win_rate") v = v == null ? "—" : (v * 100).toFixed(0) + "%";
      else if (key === "ci") v = m.ci_low == null ? "—" : `[${Math.round(m.ci_low * 100)}–${Math.round(m.ci_high * 100)}%]`;
      else if (v == null) v = "—";
      td.textContent = v;
      if (num) td.className = "num";
      tr.append(td);
    }
    tbody.append(tr);
  }
}

async function loadCharts() {
  const { models } = await (await fetch("/api/analytics")).json();
  if (!models.length) { document.getElementById("summary").textContent = "No runs yet."; return; }

  // Derive tradeoff fields, compute the efficiency frontier, render the headline.
  for (const m of models) {
    m.wall_s = m.avg_wall_clock_ms != null ? +(m.avg_wall_clock_ms / 1000).toFixed(1) : null;
  }
  frontierSet = paretoFrontier(models);
  renderFrontierNote(frontierSet);
  modelRows = models;
  renderModelsTable();

  // Strength is the headline; only show models with a fit and enough data so the bar
  // isn't dominated by provisional, small-sample entries.
  const r1 = rank(models.filter((m) => !m.low_data), "elo", false);
  barChart("c_strength", r1.map((m) => m.model), r1.map((m) => m.elo));
  const r2 = rank(models, "avg_total_tokens", true);
  barChart("c_tokens", r2.map((m) => m.model), r2.map((m) => m.avg_total_tokens));
  const r3 = rank(models, "avg_tokens_per_sec", false);
  barChart("c_speed", r3.map((m) => m.model), r3.map((m) => m.avg_tokens_per_sec));
  const r4 = rank(models, "avg_wall_clock_ms", true);
  barChart("c_wall", r4.map((m) => m.model), r4.map((m) => +(m.avg_wall_clock_ms / 1000).toFixed(1)));

  new Chart(document.getElementById("c_scatter"), {
    type: "scatter",
    data: {
      datasets: models.filter((m) => m.elo != null && !m.low_data).map((m, i) => {
        const onF = frontierSet.has(m.model);
        return {
          label: (onF ? "⭐ " : "") + m.model,
          data: [{ x: m.avg_total_tokens, y: m.elo, tps: m.avg_tokens_per_sec }],
          backgroundColor: palette[i % palette.length],
          pointRadius: onF ? 10 : 6,
          pointBorderColor: onF ? "#ffc83d" : "transparent",
          pointBorderWidth: onF ? 3 : 0,
        };
      }),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "top", labels: { boxWidth: 14, padding: 10 } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: strength ${ctx.parsed.y}, ${ctx.parsed.x.toLocaleString()} tokens, ${ctx.raw.tps ?? "—"} tok/s`,
          },
        },
      },
      scales: {
        x: { title: { display: true, text: "avg total tokens" } },
        y: { title: { display: true, text: "strength (Elo)" } },
      },
    },
  });

  const total = models.reduce((s, m) => s + m.n, 0);
  document.getElementById("summary").textContent = `${total} runs across ${models.length} models`;
}

// ---- sortable run table ----
const COLS = [
  ["id", "id", true], ["ts", "when", false], ["model", "model", false],
  ["question", "question", false], ["win", "winner", true],
  ["total_tokens", "tokens", true], ["tokens_per_sec", "tok/s", true],
  ["wall_clock_ms", "wall(ms)", true], ["tool_calls", "tools", true],
];
let runs = [];
let sortKey = "id", sortAsc = false;

function renderTable() {
  const thead = document.querySelector("#runs thead");
  const tbody = document.querySelector("#runs tbody");
  thead.innerHTML = "";
  const tr = document.createElement("tr");
  for (const [key, label, num] of COLS) {
    const th = document.createElement("th");
    th.textContent = label + (sortKey === key ? (sortAsc ? " ▲" : " ▼") : "");
    if (num) th.className = "num";
    th.onclick = () => { if (sortKey === key) sortAsc = !sortAsc; else { sortKey = key; sortAsc = false; } renderTable(); };
    tr.append(th);
  }
  thead.append(tr);

  const sorted = [...runs].sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    if (x == null) return 1; if (y == null) return -1;
    const cmp = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
    return sortAsc ? cmp : -cmp;
  });

  tbody.innerHTML = "";
  for (const r of sorted) {
    const tr = document.createElement("tr");
    for (const [key, , num] of COLS) {
      const td = document.createElement("td");
      let v = r[key];
      if (key === "question") v = (v || "").slice(0, 60);
      if (key === "ts") v = (v || "").replace("T", " ").replace("+00:00", "");
      if (key === "win") v = v ? "★ won" : "";
      td.textContent = v ?? "—";
      if (num) td.className = "num";
      tr.append(td);
    }
    tbody.append(tr);
  }
}

async function loadTable() {
  runs = (await (await fetch("/api/runs")).json()).runs;
  renderTable();
}

loadCharts();
loadTable();
