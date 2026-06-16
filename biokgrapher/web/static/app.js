"use strict";

let lastResponse = null;
let lastSource = null;
let rendered = new Set();
let activeView = "treemap";
let uploadToken = null;
let cy = null;

const $ = (id) => document.getElementById(id);
const val = (id) => $(id).value;
const SLIDERS = ["alpha", "beta", "top_k", "max_depth", "max_edges"];

document.addEventListener("DOMContentLoaded", init);

async function init() {
  checkHealth();
  await loadPresets();
  wireControls();
}

async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    if (r.ok) {
      $("health").classList.add("ok");
      $("health").title = `index ready · ${(d.global_docs || 0).toLocaleString()} documents`;
    }
  } catch (_) {}
}

async function loadPresets() {
  let data = { presets: [], terminologies: [], defaults: {} };
  try {
    data = await (await fetch("/api/presets")).json();
  } catch (_) {}
  fillSelect("preset", data.presets);
  fillSelect("terminology", data.terminologies);
  const d = data.defaults || {};
  setSlider("alpha", d.alpha ?? 0.5);
  setSlider("beta", d.beta ?? 0.5);
  setSlider("top_k", d.top_k ?? 2500);
  setSlider("max_depth", d.max_depth ?? 5);
  setSlider("max_edges", d.max_edges ?? 4000);
  if (!data.presets || !data.presets.length) {
    document.querySelector('input[name=source][value=custom]').checked = true;
    toggleSource("custom");
  }
}

function fillSelect(id, items) {
  const el = $(id);
  el.innerHTML = "";
  (items || []).forEach((v) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    el.appendChild(o);
  });
}

function setSlider(id, value) {
  $(id).value = value;
  $(id + "-v").textContent = value;
}

function wireControls() {
  document.querySelectorAll('input[name=source]').forEach((r) =>
    r.addEventListener("change", () => toggleSource(r.value)));

  SLIDERS.forEach((id) =>
    $(id).addEventListener("input", () => {
      $(id + "-v").textContent = val(id);
      if (id === "max_depth" && lastResponse && activeView !== "graph") {
        ["treemap", "sunburst", "icicle"].forEach((v) => rendered.delete(v));
        renderView(activeView);
      }
    }));

  $("pmids").addEventListener("input", () => { uploadToken = null; $("upload-info").textContent = ""; });
  $("file").addEventListener("change", uploadFile);
  // selecting a predefined preset loads its (precomputed) graph instantly
  $("preset").addEventListener("change", () => {
    if (document.querySelector('input[name=source]:checked').value === "preset") generate();
  });
  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => showView(t.dataset.view)));
  $("generate").addEventListener("click", generate);

  // show the first predefined graph on load
  if ($("preset").value) generate();
}

function toggleSource(source) {
  const custom = source === "custom";
  $("preset").disabled = custom;
  $("pmids").disabled = !custom;
  $("file").disabled = !custom;
  if (!custom) { uploadToken = null; $("upload-info").textContent = ""; }
}

async function uploadFile() {
  const f = $("file").files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  $("upload-info").textContent = "Uploading…";
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) { $("upload-info").textContent = d.detail || "Upload failed"; return; }
    uploadToken = d.pmid_token;
    $("upload-info").textContent =
      `${d.n_pmids.toLocaleString()} PMIDs loaded${d.truncated ? " (truncated)" : ""}`;
  } catch (e) {
    $("upload-info").textContent = String(e);
  }
}

function collectForm() {
  const source = document.querySelector('input[name=source]:checked').value;
  const body = {
    terminology: val("terminology"),
    alpha: +val("alpha"), beta: +val("beta"),
    top_k: +val("top_k"), max_depth: +val("max_depth"), max_edges: +val("max_edges"),
  };
  if (source === "preset") {
    if (!val("preset")) { showError("No presets available — paste PMIDs instead."); return null; }
    body.preset = val("preset");
  } else if (uploadToken) {
    body.pmid_token = uploadToken;
  } else {
    const text = $("pmids").value.trim();
    if (!text) { showError("Paste some PMIDs or upload a .txt file."); return null; }
    body.pmids = text.split(/\s+/).filter(Boolean);
  }
  return body;
}

async function generate() {
  const body = collectForm();
  if (!body) return;
  setLoading(true);
  clearMeta();
  try {
    const r = await fetch("/api/graph", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) { showError(data.detail || "Request failed"); return; }
    lastResponse = data;
    lastSource = { preset: body.preset, pmids: body.pmids, pmid_token: body.pmid_token };
    rendered = new Set();
    renderMeta(data.meta);
    renderView(activeView);
  } catch (e) {
    showError(String(e));
  } finally {
    setLoading(false);
  }
}

function showView(view) {
  activeView = view;
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === view));
  ["treemap", "sunburst", "icicle", "graph", "trends"].forEach((v) =>
    $("view-" + v).classList.toggle("hidden", v !== view));
  renderView(view);
}

function renderView(view) {
  if (!lastResponse) return;
  if (rendered.has(view)) {
    if (view === "treemap" || view === "sunburst" || view === "icicle") {
      Plotly.Plots.resize($("view-" + view));
    }
    return;
  }
  rendered.add(view);
  if (view === "graph") drawGraph("view-graph");
  else if (view === "trends") drawTrends("view-trends");
  else drawHier("view-" + view, view);
}

async function drawTrends(divId) {
  const div = $(divId);
  if (!lastSource) { div.innerHTML = emptyMsg("Generate a graph first."); return; }
  div.innerHTML = emptyMsg("Computing trends…");
  let d;
  try {
    const body = { ...lastSource, recent_years: 3, historical_years: 5, top_n: 20 };
    const r = await fetch("/api/trends", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    d = await r.json();
    if (!r.ok) { div.innerHTML = emptyMsg(d.detail || "Request failed"); return; }
  } catch (e) { div.innerHTML = emptyMsg(String(e)); return; }

  if (d.as_of === null || d.as_of === undefined) {
    div.innerHTML = emptyMsg("No publication-year data in the index — rebuild to capture dates.");
    return;
  }
  const items = [...d.declining].reverse().concat([...d.trending].reverse());
  if (!items.length) { div.innerHTML = emptyMsg("Not enough dated documents for a trend."); return; }
  div.innerHTML = "";
  Plotly.react(div, [{
    type: "bar", orientation: "h",
    x: items.map((i) => i.rate), y: items.map((i) => i.name),
    marker: { color: items.map((i) => (i.rate >= 0 ? "#3fb950" : "#bd0026")) },
    hovertemplate: "%{y}<br>rate %{x:+.0%}<extra></extra>",
  }], {
    margin: { l: 220, t: 36, r: 20, b: 36 }, height: Math.max(360, items.length * 26),
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: "#e6edf3" },
    title: `Concept trend — recent (${d.recent_from}+) vs historical (${d.hist_from}–${d.recent_from - 1})`,
    xaxis: { title: "rate of change", tickformat: "+.0%", zeroline: true, zerolinecolor: "#444" },
    yaxis: { automargin: true },
  }, { responsive: true, displaylogo: false });
}

function wrap(text, width = 90) {
  if (!text) return "";
  const out = [];
  let line = "";
  for (const word of String(text).split(/\s+/)) {
    if ((line + " " + word).trim().length > width) { out.push(line.trim()); line = word; }
    else line += " " + word;
  }
  if (line.trim()) out.push(line.trim());
  return out.join("<br>");
}

function drawHier(divId, type) {
  const div = $(divId);
  const h = lastResponse.hierarchy;
  if (!h || !h.length) { div.innerHTML = emptyMsg("No hierarchy available for this terminology."); return; }
  const trace = {
    type,
    ids: h.map((n) => n.id),
    labels: h.map((n) => n.name),
    parents: h.map((n) => n.parent),
    customdata: h.map((n) => [(+n.score).toFixed(4), wrap(n.explanation)]),
    marker: {
      colors: h.map((n) => +n.score), colorscale: "YlOrRd", cmin: 0,
      showscale: true, colorbar: { title: "score", thickness: 12, x: 1.0 },
      line: { width: 0.5, color: "#0f1216" },
    },
    hovertemplate: "<b>%{label}</b><br>score %{customdata[0]}<br>%{customdata[1]}<extra></extra>",
    maxdepth: +val("max_depth"),
  };
  if (type === "icicle") trace.tiling = { orientation: "h" };
  if (type === "sunburst") trace.insidetextorientation = "radial";
  const layout = { margin: { t: 20, l: 8, r: 8, b: 8 }, paper_bgcolor: "rgba(0,0,0,0)", font: { color: "#e6edf3" } };
  Plotly.react(div, [trace], layout, { responsive: true, displaylogo: false });
}

function drawGraph(divId) {
  const div = $(divId);
  const r = lastResponse;
  if (!r.graph_edges || !r.graph_edges.length) {
    div.innerHTML = emptyMsg("No semantic relations among the top concepts.");
    return;
  }
  div.innerHTML = "";
  const smax = Math.max(0.0001, ...r.graph_nodes.map((n) => +n.score || 0));
  const elements = [];
  r.graph_nodes.forEach((n) => elements.push({ data: { id: n.cui, label: n.name, score: +n.score || 0 } }));
  r.graph_edges.forEach((e, i) =>
    elements.push({ data: { id: "e" + i, source: e.source, target: e.target, label: e.relation } }));
  if (cy) cy.destroy();
  cy = cytoscape({
    container: div,
    elements,
    style: [
      { selector: "node", style: {
        "label": "data(label)", "font-size": 7, "color": "#c9d1d9",
        "text-wrap": "ellipsis", "text-max-width": 80, "min-zoomed-font-size": 6,
        "width": `mapData(score, 0, ${smax}, 10, 55)`,
        "height": `mapData(score, 0, ${smax}, 10, 55)`,
        "background-color": `mapData(score, 0, ${smax}, #ffeda0, #bd0026)` } },
      { selector: "edge", style: {
        "label": "data(label)", "font-size": 6, "color": "#6e7681", "curve-style": "bezier",
        "width": 1, "line-color": "#30363d", "target-arrow-shape": "triangle",
        "target-arrow-color": "#30363d", "text-rotation": "autorotate",
        "min-zoomed-font-size": 7 } },
      { selector: ".faded", style: { "opacity": 0.12 } },
      { selector: ":selected", style: { "border-width": 2, "border-color": "#2f81f7" } },
    ],
    layout: { name: "cose", animate: false, nodeRepulsion: 8000, idealEdgeLength: 60 },
    wheelSensitivity: 0.2,
  });
  cy.on("tap", "node", async (evt) => {
    const node = evt.target;
    cy.elements().addClass("faded");
    node.closedNeighborhood().removeClass("faded");
    try {
      const d = await (await fetch("/api/concept/" + encodeURIComponent(node.id()))).json();
      showPanel(d.name, d.cui, d.definition);
    } catch (_) {}
  });
  cy.on("tap", (evt) => {
    if (evt.target === cy) { cy.elements().removeClass("faded"); hidePanel(); }
  });
}

function showPanel(name, cui, definition) {
  const p = $("panel");
  p.innerHTML =
    `<button onclick="document.getElementById('panel').classList.add('hidden')">×</button>` +
    `<h3>${escapeHtml(name)}</h3><div class="cui">${escapeHtml(cui)}</div>` +
    `<p>${escapeHtml(definition) || "<span class='cui'>No definition available.</span>"}</p>`;
  p.classList.remove("hidden");
}
function hidePanel() { $("panel").classList.add("hidden"); }

function renderMeta(m) {
  const parts = [
    `${m.n_pmids_matched.toLocaleString()} / ${m.n_pmids_input.toLocaleString()} PMIDs matched`,
    `${m.n_scored.toLocaleString()} concepts scored`,
    `${m.n_hier_nodes.toLocaleString()} hierarchy nodes`,
    `${m.n_graph_nodes.toLocaleString()} graph nodes / ${m.n_graph_edges.toLocaleString()} edges${m.edges_truncated ? " (capped)" : ""}`,
    `${m.elapsed_ms} ms`,
  ];
  let html = parts.join(" · ");
  for (const w of m.warnings || []) html += ` <span class="warn">⚠ ${escapeHtml(w)}</span>`;
  $("meta").innerHTML = html;
}

function clearMeta() { $("meta").innerHTML = ""; }
function showError(msg) { $("meta").innerHTML = `<span class="err">✕ ${escapeHtml(msg)}</span>`; }
function setLoading(on) { $("overlay").classList.toggle("hidden", !on); $("generate").disabled = on; }
function emptyMsg(text) { return `<div style="padding:40px;color:#8b949e;text-align:center">${escapeHtml(text)}</div>`; }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
