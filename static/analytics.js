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

async function loadCharts() {
  const { models } = await (await fetch("/api/analytics")).json();
  if (!models.length) { document.getElementById("summary").textContent = "No runs yet."; return; }

  const r1 = rank(models, "avg_rating", false);
  barChart("c_rating", r1.map((m) => m.model), r1.map((m) => m.avg_rating));
  const r2 = rank(models, "avg_total_tokens", true);
  barChart("c_tokens", r2.map((m) => m.model), r2.map((m) => m.avg_total_tokens));
  const r3 = rank(models, "avg_tokens_per_sec", false);
  barChart("c_speed", r3.map((m) => m.model), r3.map((m) => m.avg_tokens_per_sec));
  const r4 = rank(models, "avg_wall_clock_ms", true);
  barChart("c_wall", r4.map((m) => m.model), r4.map((m) => +(m.avg_wall_clock_ms / 1000).toFixed(1)));
  const r5 = rank(models.filter((m) => m.source_votes > 0), "source_credible_pct", false);
  if (r5.length) barChart("c_cred", r5.map((m) => m.model), r5.map((m) => m.source_credible_pct));

  new Chart(document.getElementById("c_scatter"), {
    type: "scatter",
    data: {
      datasets: models.filter((m) => m.avg_rating != null).map((m, i) => ({
        label: m.model,
        data: [{ x: m.avg_total_tokens, y: m.avg_rating }],
        backgroundColor: palette[i % palette.length],
        pointRadius: 7,
      })),
    },
    options: {
      scales: {
        x: { title: { display: true, text: "avg total tokens" } },
        y: { title: { display: true, text: "avg rating" }, min: 0, max: 5 },
      },
    },
  });

  const total = models.reduce((s, m) => s + m.n, 0);
  document.getElementById("summary").textContent = `${total} runs across ${models.length} models`;
}

// ---- sortable run table ----
const COLS = [
  ["id", "id", true], ["ts", "when", false], ["model", "model", false],
  ["question", "question", false], ["rating", "rating", true],
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
      if (key === "rating") v = v == null ? "—" : "★".repeat(v);
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
