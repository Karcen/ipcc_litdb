/* ===========================================================================
   IPCC Literature Database frontend.
   Loads dashboard data from /api/* and uses local browser libraries.
   =========================================================================== */
"use strict";

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (n) => (n === null || n === undefined || n === "") ? "—" : Number(n).toLocaleString("en-US");
const pct = (n) => (n === null || n === undefined) ? "—" : Number(n).toFixed(1) + "%";
const valueLabel = (n, unit = "") => (n === null || n === undefined || n === "") ? "—" : nf(n) + unit;

/* ------------------------------------------------------------------ i18n */
const LOCALE = "en";
let DICT = {};
async function loadEnglish() {
  DICT = await api("/static/locales/en.json");
  document.documentElement.lang = LOCALE;
  translate();
}
const t = (key) => DICT[key] ?? key;
function translate() {
  $$("[data-i18n]").forEach((el) => {
    const v = DICT[el.dataset.i18n]; if (v !== undefined) el.textContent = v;
  });
  $$("[data-i18n-ph]").forEach((el) => {
    const v = DICT[el.dataset.i18nPh]; if (v !== undefined) el.placeholder = v;
  });
}

/* ---------------------------------------------------------------- themes */
const SUN = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/></svg>`;
const MOON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`;
function applyTheme(mode, persist = true) {
  const dark = mode === "dark" ||
    (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  if (persist) localStorage.setItem("ipcc.theme", mode);
  $("#theme").value = mode; $("#set-theme").value = mode;
  rerender();                       // charts need repainting for the new palette
}
window.matchMedia("(prefers-color-scheme: dark)")
  .addEventListener("change", () => { if (localStorage.getItem("ipcc.theme") === "system") applyTheme("system", false); });
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

/* ------------------------------------------------------------------ fetch */
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}
function fail(e) {
  const b = $("#error");
  b.textContent = `${t("state.error")}: ${e.message}`;
  b.classList.remove("hidden"); console.error(e);
}
const clearFail = () => $("#error").classList.add("hidden");

/* ----------------------------------------------------------------- charts */
function barChart(el, rows, { labelKey, valueKey, color, height, max, unit = "", exportInfo }) {
  if (!el) return;
  const data = rows.filter((r) => r[valueKey] !== null && r[valueKey] !== undefined &&
    r[valueKey] !== "" && Number.isFinite(Number(r[valueKey])) && Number(r[valueKey]) >= 0);
  if (!data.length) {
    el.innerHTML = `<div class="empty">${esc(t("state.empty"))}</div>`;
    registerChart(el, rows, exportInfo); return;
  }
  color = color || css("--accent");
  const w = Math.max(el.clientWidth || 460, 320), padR = 60, padT = 4, padB = 4;
  const shortLabel = (value) => String(value ?? "").length > 16 ? String(value).slice(0, 15) + "…" : String(value ?? "");
  const measure = document.createElement("canvas").getContext("2d"); measure.font = "10.5px Arial";
  const padL = Math.max(104, Math.ceil(Math.max(...data.map((row) => measure.measureText(shortLabel(row[labelKey])).width))) + 14);
  const bh = Math.max(11, Math.min(21, ((height || 190) - padT - padB) / data.length - 3));
  const H = data.length * (bh + 3) + padT + padB;
  const top = max || Math.max(1, ...data.map((r) => Number(r[valueKey])));
  const plotW = w - padL - padR;
  let s = `<svg width="${w}" height="${H}" role="img" font-family="Arial, sans-serif">`;
  data.forEach((r, i) => {
    const v = Number(r[valueKey]), bw = v === 0 ? 0 : Math.max(2, plotW * (v / top));
    const y = padT + i * (bh + 3);
    const label = String(r[labelKey] ?? "");
    s += `<text class="bar-label" x="${padL - 7}" y="${y + bh - 3}" text-anchor="end">${
      esc(shortLabel(label))}</text>`;
    s += `<rect x="${padL}" y="${y}" width="${bw}" height="${bh}" rx="3" fill="${color}"
            data-tip="${esc(label)}: ${valueLabel(v, unit)}"></rect>`;
    s += `<text class="bar-val" x="${padL + bw + 6}" y="${y + bh - 3}">${valueLabel(v, unit)}</text>`;
  });
  el.innerHTML = s + "</svg>";
  registerChart(el, rows, exportInfo);
}

function lineChart(el, rows, { xKey, yKey, height = 200, color, unit = "", max, exportInfo }) {
  if (!el) return;
  const data = rows.filter((r) => r[xKey] !== null && r[xKey] !== undefined && r[xKey] !== "" &&
      r[yKey] !== null && r[yKey] !== undefined && r[yKey] !== "")
    .map((r) => ({ x: Number(r[xKey]), y: Number(r[yKey]) }))
    .filter((d) => Number.isFinite(d.x) && Number.isFinite(d.y)).sort((a, b) => a.x - b.x);
  if (!data.length) {
    el.innerHTML = `<div class="empty">${esc(t("state.empty"))}</div>`;
    registerChart(el, rows, exportInfo); return;
  }
  color = color || css("--ok");
  const w = Math.max(el.clientWidth || 460, 320), h = height;
  const padL = unit === "%" ? 52 : 44, padR = 12, padT = 8, padB = 24;
  const xs = data.map((d) => d.x), ys = data.map((d) => d.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y1 = max || Math.max(...ys);
  const px = (x) => padL + (w - padL - padR) * ((x - x0) / Math.max(x1 - x0, 1));
  const py = (y) => h - padB - (h - padT - padB) * (y / Math.max(y1, 1));
  let s = `<svg width="${w}" height="${h}">`;
  for (let g = 0; g <= 4; g++) {
    const yv = y1 * g / 4, yy = py(yv);
    s += `<line x1="${padL}" y1="${yy}" x2="${w - padR}" y2="${yy}" stroke="${css("--grid")}"/>`;
    s += `<text class="bar-label" x="${padL - 6}" y="${yy + 3.5}" text-anchor="end">${valueLabel(Math.round(yv), unit)}</text>`;
  }
  const maxTicks = Math.max(4, Math.floor((w - padL - padR) / 58));
  const step = Math.max(1, Math.ceil(data.length / maxTicks));
  data.forEach((d, i) => {
    if (i % step === 0 || i === data.length - 1)
      s += `<text class="bar-label" x="${px(d.x)}" y="${h - 7}" text-anchor="middle">${d.x}</text>`;
  });
  s += `<polyline fill="none" stroke="${color}" stroke-width="1.7"
          points="${data.map((d) => `${px(d.x)},${py(d.y)}`).join(" ")}"/>`;
  data.forEach((d) => {
    s += `<circle cx="${px(d.x)}" cy="${py(d.y)}" r="2.1" fill="${color}"
            data-tip="${d.x}: ${valueLabel(d.y, unit)}"/>`;
  });
  el.innerHTML = s + "</svg>";
  registerChart(el, rows, exportInfo);
}

/* --------------------------------------------------------- figure downloads */
const CHART_EXPORTS = new Map();
function registerChart(el, rows, info) {
  if (!info) return;
  const entry = { ...info, rows: info.rows || rows, source_generated_at: info.source_generated_at || new Date().toISOString() };
  CHART_EXPORTS.set(el.id, entry);
  const figure = !!$("svg", el);
  el.insertAdjacentHTML("beforeend", `<div class="chart-downloads" aria-label="${esc(t("export.downloads"))}">
    <span>${esc(entry.unit || "")}</span>` + ["csv", "png", "svg", "json"].map((format) =>
      `<button data-chart-download="${format}" data-chart-id="${esc(el.id)}" ${!figure && ["png", "svg"].includes(format) ? "disabled" : ""}
        title="${esc(t("export." + format))}">${format.toUpperCase()}</button>`).join("") + "</div>");
}
function exportDocument(entry) {
  return { provenance: {
    dataset: "IPCC Literature Database", title: entry.title, unit: entry.unit,
    generated_at: new Date().toISOString(), source_generated_at: entry.source_generated_at,
    source: entry.source, filters: entry.filters || {}, sql: entry.sql || null,
    row_count: entry.rows.length, ...entry.provenance,
  }, columns: Object.keys(entry.rows[0] || {}), rows: entry.rows };
}
function csvDocument(document) {
  const cell = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  return "\ufeff# provenance: " + JSON.stringify(document.provenance) + "\n" +
    [document.columns.map(cell).join(","), ...document.rows.map((row) =>
      document.columns.map((column) => cell(row[column])).join(","))].join("\r\n") + "\r\n";
}
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function wrapCaption(text, width) {
  const size = Math.max(25, Math.floor(width / 7)), lines = [];
  let line = "";
  for (const word of String(text).split(/\s+/)) {
    if (line && line.length + word.length + 1 > size) { lines.push(line); line = ""; }
    line += (line ? " " : "") + word;
  }
  if (line) lines.push(line);
  return lines;
}
function standaloneSvg(svg, entry) {
  const clone = svg.cloneNode(true);
  const originals = [svg, ...svg.querySelectorAll("*")], copies = [clone, ...clone.querySelectorAll("*")];
  originals.forEach((node, index) => {
    const style = getComputedStyle(node);
    for (const property of ["fill", "stroke", "stroke-width", "font-size", "font-weight", "text-anchor", "opacity"]) {
      copies[index].style.setProperty(property, style.getPropertyValue(property));
    }
    copies[index].style.fontFamily = "Arial, sans-serif";
  });
  const width = Number(svg.getAttribute("width")) || svg.viewBox.baseVal.width || 800;
  const height = Number(svg.getAttribute("height")) || svg.viewBox.baseVal.height || 400;
  const document = exportDocument(entry), filters = Object.entries(entry.filters || {})
    .filter(([, value]) => value !== "" && value !== null && value !== undefined)
    .map(([key, value]) => `${key}=${value}`).join("; ") || "All records";
  const headings = wrapCaption(entry.title, width), captions = wrapCaption(`${entry.unit || ""} · ${filters}`, width);
  const footers = wrapCaption(`Source: IPCC Literature Database ${entry.source || ""} · Retrieved ${entry.source_generated_at}`, width);
  const top = 20 + 19 * headings.length + 15 * captions.length;
  const totalHeight = top + height + 24 + 15 * footers.length;
  const text = (lines, y, size, color, weight = 400) => lines.map((line, index) =>
    `<text x="24" y="${y + index * (size + 4)}" font-family="Arial, sans-serif" font-size="${size}" font-weight="${weight}" fill="${color}">${esc(line)}</text>`).join("");
  const contents = new XMLSerializer().serializeToString(clone);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width + 48}" height="${totalHeight}" viewBox="0 0 ${width + 48} ${totalHeight}">
    <title>${esc(entry.title)}</title><desc>${esc(entry.unit || "")}; ${esc(filters)}</desc>
    <metadata>${esc(JSON.stringify(document.provenance))}</metadata>
    <rect width="100%" height="100%" fill="${css("--panel")}"/>
    ${text(headings, 25, 15, css("--fg"), 600)}
    ${text(captions, 25 + 19 * headings.length, 11, css("--muted"))}
    <g transform="translate(24 ${top})">${contents}</g>
    ${text(footers, top + height + 20, 11, css("--muted"))}</svg>`;
}
async function svgToPng(source) {
  const url = URL.createObjectURL(new Blob([source], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const picture = new Image();
    await new Promise((resolve, reject) => { picture.onload = resolve; picture.onerror = () => reject(new Error("Could not render the figure.")); picture.src = url; });
    const canvas = document.createElement("canvas"), scale = 2;
    canvas.width = picture.naturalWidth * scale; canvas.height = picture.naturalHeight * scale;
    canvas.getContext("2d").drawImage(picture, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Could not save the figure.")), "image/png"));
  } finally { URL.revokeObjectURL(url); }
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-chart-download]"); if (!button) return;
  const entry = CHART_EXPORTS.get(button.dataset.chartId), format = button.dataset.chartDownload;
  button.disabled = true;
  try {
    const name = `ipcc_${button.dataset.chartId}.${format}`, document = exportDocument(entry);
    if (format === "csv") downloadBlob(new Blob([csvDocument(document)], { type: "text/csv;charset=utf-8" }), name);
    else if (format === "json") downloadBlob(new Blob([JSON.stringify(document, null, 2)], { type: "application/json" }), name);
    else {
      const source = standaloneSvg($("#" + button.dataset.chartId + " svg"), entry);
      downloadBlob(format === "png" ? await svgToPng(source) : new Blob([source], { type: "image/svg+xml;charset=utf-8" }), name);
    }
  } catch (error) { fail(error); }
  finally { button.disabled = false; }
});

function progressBar(p) {
  const v = Math.max(0, Math.min(100, Number(p) || 0));
  return `<span class="bar-inline" style="width:${(v * 0.55).toFixed(1)}px"></span>
          <span style="color:var(--muted);font-size:11px;margin-left:5px">${v.toFixed(1)}%</span>`;
}

/* tooltip */
const tip = document.createElement("div");
tip.style.cssText = "position:fixed;pointer-events:none;background:var(--panel);border:1px solid var(--line);" +
  "color:var(--fg);padding:4px 8px;border-radius:6px;font-size:11.5px;z-index:999;display:none;box-shadow:var(--shadow)";
document.body.appendChild(tip);
document.addEventListener("mousemove", (e) => {
  const n = e.target.closest?.("[data-tip]");
  if (n) { tip.textContent = n.getAttribute("data-tip"); tip.style.display = "block";
           tip.style.left = (e.clientX + 12) + "px"; tip.style.top = (e.clientY + 12) + "px"; }
  else tip.style.display = "none";
});

/* ---------------------------------------------------------------- routing */
const LOADED = {};
function show(view) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  location.hash = "#/" + view;
  const loaders = {
    reports: loadReports, literature: () => loadPapers(1), bi: initBI,
    analytics: loadAnalyticsIndex, insights: loadInsights,
    networks: () => loadNetwork(true), quality: loadQuality,
    unresolved: () => loadUnresolved(1), progress: loadProgress,
    ai: loadAIStatus, settings: loadSettings,
  };
  if (view !== "overview" && loaders[view] && !LOADED[view]) { LOADED[view] = true; loaders[view](); }
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

function rerender() {
  const active = $$("#tabs button").find((b) => b.classList.contains("active"))?.dataset.view;
  if (active === "overview") loadOverview();
  else if (active === "networks") renderNetwork();
  else if (active === "bi" && BI_RESULT) renderBI(BI_RESULT);
}

/* ------------------------------------------------------------------ modal */
function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* ------------------------------------------------------------------ overview */
let STATS = null;
async function loadOverview() {
  const s = await api("/api/statistics"); STATS = s;
  const x = s.totals;
  $("#buildmeta").textContent = s.generated_at || "";
  const K = [
    ["kpi.total_papers", x.papers, ""], ["kpi.total_references", x.references, ""],
    ["kpi.reports", x.reports, ""], ["kpi.working_groups", x.working_groups, ""],
    ["kpi.chapters", x.chapters, ""], ["kpi.matched", x.matched, "ok"],
    ["kpi.unmatched", x.unmatched, "warn"], ["kpi.unique_doi", x.unique_doi, ""],
    ["kpi.missing_doi", x.missing_doi, "warn"], ["kpi.duplicates", x.duplicate_title_groups, "warn"],
    ["kpi.manual_review", x.manual_review_required, "bad"], ["kpi.wos_records", x.wos_records, ""],
    ["kpi.multi_report", x.multi_report_papers, ""], ["kpi.quality_flags", x.quality_flags, "warn"],
  ];
  $("#kpis").innerHTML = K.map(([k, v, c]) =>
    `<div class="kpi ${c}"><div class="v">${nf(v)}</div><div class="l">${esc(t(k))}</div></div>`).join("");
  const overviewExport = (title, unit, filters = {}) => ({ title: t(title), unit,
    source: "/api/statistics", source_generated_at: s.generated_at, filters });
  barChart($("#chart-reports"), s.by_report, { labelKey: "report_id", valueKey: "n_references",
    exportInfo: overviewExport("chart.by_report", "Reference occurrences") });
  barChart($("#chart-wg"), s.by_working_group.filter((r) => r.n_references > 0)
    .sort((a, b) => b.n_references - a.n_references).slice(0, 18)
    .map((row) => ({ ...row, report_working_group: `${row.report_id} ${row.wg_label}` })),
    { labelKey: "report_working_group", valueKey: "n_references",
      exportInfo: overviewExport("chart.by_wg", "Reference occurrences", { top_groups: 18 }) });
  const MIN_YEAR = 1850;
  lineChart($("#chart-year"), s.by_year.filter((r) => Number(r.year) >= MIN_YEAR),
            { xKey: "year", yKey: "n_papers",
              exportInfo: overviewExport("chart.by_year", "Unique papers", { year_from: MIN_YEAR }) });
  barChart($("#chart-completeness"), Object.entries(s.completeness).map(([k, v]) => ({ k, v })),
           { labelKey: "k", valueKey: "v", color: css("--warn"), max: 100, height: 110, unit: "%",
             exportInfo: { ...overviewExport("chart.completeness", "% of reference occurrences"),
               provenance: { denominator: x.references } } });
  $("#chart-match").innerHTML =
    `<div style="font-size:19px;font-weight:660;color:var(--ok)">${pct(x.completion_rate)}</div>
     <div class="hint" style="margin:2px 0 8px">${nf(x.matched)} / ${nf(x.references)}</div>`;
  const top = await api("/api/papers?min_reports=2&sort=n_reports&size=15");
  $("#tbl-multireport").innerHTML =
    `<thead><tr><th>${t("table.title")}</th><th>${t("table.year")}</th><th>${t("table.journal")}</th>
      <th class="num">${t("table.reports")}</th><th class="num">${t("table.chapters")}</th>
      <th class="num">${t("table.occurrences")}</th></tr></thead><tbody>` +
    top.items.map((p) => `<tr class="rowlink" data-paper="${p.paper_id}">
      <td>${esc(p.title_best || "—")}</td><td>${esc(p.year_best || "—")}</td>
      <td>${esc((p.journal_best || "—").slice(0, 44))}</td>
      <td class="num">${nf(p.n_reports)}</td><td class="num">${nf(p.n_chapters)}</td>
      <td class="num">${nf(p.n_occurrences)}</td></tr>`).join("") + "</tbody>";
}

/* ------------------------------------------------------------------- reports */
let FILTER_CATALOG = null, FILTERS_READY = false;
async function loadFilterOptions() {
  FILTER_CATALOG ||= Promise.all([api("/api/reports"), api("/api/working-groups")]);
  const [reps, wgs] = await FILTER_CATALOG;
  if (FILTERS_READY) return [reps, wgs];
  const opt = reps.map((r) => `<option value="${esc(r.report_id)}">${esc(r.report_id)}</option>`).join("");
  $("#f-report").innerHTML = `<option value="">${t("filter.all_reports")}</option>` + opt;
  $("#u-report").innerHTML = `<option value="">${t("filter.all_reports")}</option>` + opt;
  $("#bi-report").innerHTML = `<option value="">${t("filter.all_reports")}</option>` + opt;
  const wgl = [...new Set(wgs.map((w) => w.wg_label))].sort();
  const wopt = wgl.map((w) => `<option value="${esc(w)}">${esc(w)}</option>`).join("");
  $("#f-wg").innerHTML = `<option value="">${t("filter.all_wg")}</option>` + wopt;
  $("#bi-wg").innerHTML = `<option value="">${t("filter.all_wg")}</option>` + wopt;
  FILTERS_READY = true;
  return [reps, wgs];
}
async function loadReports() {
  const [reps] = await loadFilterOptions();
  let html = `<div class="trow head"><div>Report / WG / Chapter</div>
    <div class="num">${t("table.refs")}</div><div class="num">${t("table.matched")}</div>
    <div class="num">${t("table.unmatched")}</div><div class="num">${t("table.missing_doi")}</div>
    <div class="num">${t("table.completion")}</div></div>`;
  for (const r of reps) {
    const chs = await api(`/api/chapters?report_id=${encodeURIComponent(r.report_id)}&limit=1000`);
    const sum = (f) => chs.reduce((a, c) => a + (c[f] || 0), 0);
    const nRefs = sum("n_references"); if (!nRefs) continue;
    const nMat = sum("matched"), nUn = sum("unmatched"), nMd = sum("missing_doi");
    html += `<div class="trow lvl1" data-toggle="r-${esc(r.report_id)}"><div>${esc(r.report_id)}</div>
      <div class="num">${nf(nRefs)}</div><div class="num">${nf(nMat)}</div>
      <div class="num">${nf(nUn)}</div><div class="num">${nf(nMd)}</div>
      <div class="num">${progressBar(100 * nMat / nRefs)}</div></div>`;
    const byWg = {}; chs.forEach((c) => (byWg[c.wg_label] ||= []).push(c));
    for (const [wg, list] of Object.entries(byWg)) {
      const S = (f) => list.reduce((a, c) => a + (c[f] || 0), 0);
      const a = S("n_references"), b = S("matched");
      const rid = `r-${r.report_id}-${wg.replace(/[^A-Za-z0-9]/g, "")}`;
      html += `<div class="trow lvl2 hidden" data-parent="r-${esc(r.report_id)}" data-toggle="${esc(rid)}">
        <div>${esc(wg)}</div><div class="num">${nf(a)}</div><div class="num">${nf(b)}</div>
        <div class="num">${nf(S("unmatched"))}</div><div class="num">${nf(S("missing_doi"))}</div>
        <div class="num">${progressBar(100 * b / Math.max(a, 1))}</div></div>`;
      list.forEach((c) => {
        html += `<div class="trow lvl3 hidden" data-parent="${esc(rid)}" data-chapter="${esc(c.chapter_id)}">
          <div>${esc(c.chapter_label)} — ${esc((c.chapter_title || "").slice(0, 66))}</div>
          <div class="num">${nf(c.n_references)}</div><div class="num">${nf(c.matched)}</div>
          <div class="num">${nf(c.unmatched)}</div><div class="num">${nf(c.missing_doi)}</div>
          <div class="num">${progressBar(100 * c.matched / Math.max(c.n_references, 1))}</div></div>`;
      });
    }
  }
  $("#tree-reports").innerHTML = html;
  $$("#tree-reports .trow[data-toggle]").forEach((row) => row.addEventListener("click", (e) => {
    e.stopPropagation();
    $$(`#tree-reports .trow[data-parent="${CSS.escape(row.dataset.toggle)}"]`)
      .forEach((c) => c.classList.toggle("hidden"));
  }));
  $$("#tree-reports .trow[data-chapter]").forEach((row) => row.addEventListener("click", (e) => {
    e.stopPropagation(); loadChapterRefs(row.dataset.chapter);
  }));
}
async function loadChapterRefs(cid) {
  const d = await api(`/api/references?chapter_id=${encodeURIComponent(cid)}&size=200`);
  const f = d.items[0] || {};
  $("#chapter-refs-title").textContent =
    `${f.report_id || ""} ${f.wg_label || ""} ${f.chapter_label || ""} — ${f.chapter_title || ""}`;
  $("#chapter-refs").innerHTML = refTable(d.items);
  $("#card-chapter-refs").classList.remove("hidden");
  $("#card-chapter-refs").scrollIntoView({ behavior: "smooth", block: "start" });
}
function statusChip(s) {
  if (s === "matched_doi") return `<span class="chip ok">${t("status.matched_doi")}</span>`;
  if (s === "matched_title") return `<span class="chip ok">${t("status.matched_title")}</span>`;
  return `<span class="chip bad">${t("status.unresolved")}</span>`;
}
function verifChip(v) {
  if (v === "wos_matched") return `<span class="chip ok">${t("status.wos_matched")}</span>`;
  if (v === "doi_verified") return `<span class="chip info">${t("status.doi_verified")}</span>`;
  return `<span class="chip bad">${t("status.unresolved")}</span>`;
}
function refTable(items) {
  if (!items.length) return `<div class="empty">${esc(t("state.empty"))}</div>`;
  return `<div class="tblwrap"><table class="tbl"><thead><tr>
    <th>${t("table.page")}</th><th>${t("table.parsed_title")}</th><th>${t("table.authors")}</th>
    <th>${t("table.year")}</th><th>${t("table.doi")}</th><th>${t("table.match_status")}</th>
    <th>${t("table.original")}</th></tr></thead><tbody>` +
    items.map((r) => `<tr><td>${esc(r.source_page ?? "—")}</td>
      <td>${esc((r.parsed_title || "—").slice(0, 88))}</td>
      <td>${esc((r.parsed_authors || "—").slice(0, 44))}</td>
      <td>${esc(r.parsed_year || "—")}</td>
      <td>${r.doi_norm ? `<a href="https://doi.org/${esc(r.doi_norm)}" target="_blank" rel="noopener">${esc(r.doi_norm)}</a>` : "—"}</td>
      <td>${statusChip(r.match_status)}</td>
      <td title="${esc(r.raw_reference || "")}">${esc((r.raw_reference || "").slice(0, 110))}</td>
    </tr>`).join("") + "</tbody></table></div>";
}

/* ---------------------------------------------------------------- literature */
let PAPER_QUERY = null, UNRESOLVED_QUERY = null;
function litQuery(page) {
  const p = new URLSearchParams({ page, size: 25 });
  const q = $("#f-q").value.trim(), doi = $("#f-doi").value.trim();
  if (q) p.set("q", q);
  if (doi) p.set("doi", doi);
  if ($("#f-report").value) p.set("report_id", $("#f-report").value);
  if ($("#f-wg").value) p.set("wg", $("#f-wg").value);
  if ($("#f-year-from").value) p.set("year_from", $("#f-year-from").value);
  if ($("#f-year-to").value) p.set("year_to", $("#f-year-to").value);
  if ($("#f-status").value) p.set("status", $("#f-status").value);
  const mr = $("#f-minreports").value;
  if (mr) p.set("min_reports", String(Number(mr) + 1));
  p.set("sort", $("#f-sort").value || "n_reports");
  return p.toString();
}
async function loadPapers(page = 1) {
  await loadFilterOptions();
  const query = litQuery(page);
  const d = await api("/api/papers?" + query);
  PAPER_QUERY = query;
  $("#lit-summary").textContent =
    `${nf(d.total)} — page ${d.page} / ${Math.max(1, Math.ceil(d.total / d.size))}`;
  $("#tbl-papers").innerHTML =
    `<thead><tr><th>${t("table.title")}</th><th>${t("table.authors")}</th><th>${t("table.year")}</th>
      <th>${t("table.journal")}</th><th>${t("table.doi")}</th><th class="num">${t("table.reports")}</th>
      <th class="num">${t("table.chapters")}</th><th>${t("table.status")}</th></tr></thead><tbody>` +
    (d.items.length ? d.items.map((p) => `<tr class="rowlink" data-paper="${p.paper_id}">
      <td>${esc(p.title_best || "—")}</td><td>${esc((p.authors_best || "—").slice(0, 56))}</td>
      <td>${esc(p.year_best || "—")}</td><td>${esc((p.journal_best || "—").slice(0, 38))}</td>
      <td>${p.doi_norm ? `<a href="https://doi.org/${esc(p.doi_norm)}" target="_blank" rel="noopener">${esc(p.doi_norm)}</a>` : "—"}</td>
      <td class="num">${nf(p.n_reports)}</td><td class="num">${nf(p.n_chapters)}</td>
      <td>${verifChip(p.verification_status)}</td></tr>`).join("")
      : `<tr><td colspan="8"><div class="empty">${esc(t("state.empty"))}</div></td></tr>`) + "</tbody>";
  $("#pager-papers").innerHTML = pagerHTML(d);
  bindPager("#pager-papers", loadPapers);
  $$("#pager-papers button").forEach((b) => b.addEventListener("click", () => {}));
}
function pagerHTML(d) {
  const pages = Math.max(1, Math.ceil(d.total / d.size));
  return `<span>${nf(d.total)}</span>
    <button data-pg="1" ${d.page <= 1 ? "disabled" : ""}>« ${t("btn.first")}</button>
    <button data-pg="${d.page - 1}" ${d.page <= 1 ? "disabled" : ""}>‹ ${t("btn.prev")}</button>
    <span>${d.page} / ${pages}</span>
    <button data-pg="${d.page + 1}" ${d.page >= pages ? "disabled" : ""}>${t("btn.next")} ›</button>
    <button data-pg="${pages}" ${d.page >= pages ? "disabled" : ""}>${t("btn.last")} »</button>`;
}
function bindPager(sel, fn) {
  $$(sel + " button").forEach((b) => b.addEventListener("click", () => fn(Number(b.dataset.pg))));
}
$("#btn-search").addEventListener("click", () => loadPapers(1));
$("#btn-reset").addEventListener("click", () => {
  ["#f-q", "#f-doi", "#f-year-from", "#f-year-to"].forEach((s) => ($(s).value = ""));
  ["#f-report", "#f-wg", "#f-status", "#f-minreports"].forEach((s) => ($(s).value = ""));
  $("#f-sort").value = "n_reports"; loadPapers(1);
});
["#f-q", "#f-doi"].forEach((s) => $(s).addEventListener("keydown",
  (e) => { if (e.key === "Enter") loadPapers(1); }));

/* ------------------------------------------------------------------- BI */
let BI_META = null, BI_RESULT = null;
async function initBI() {
  await loadFilterOptions();
  if (!BI_META) BI_META = await api("/api/metrics");
  $("#bi-metric").innerHTML = BI_META.metrics.map((m) =>
    `<option value="${esc(m.key)}">${esc(m.label.en)}</option>`).join("");
  $("#bi-dim").innerHTML = `<option value="">${t("filter.none")}</option>` +
    BI_META.dimensions.map((d) =>
      `<option value="${esc(d.key)}">${esc(d.label.en)}</option>`).join("");
  $("#bi-run").onclick = runBI;
  runBI();
}
async function runBI() {
  const p = new URLSearchParams({ metric: $("#bi-metric").value });
  if ($("#bi-dim").value) p.set("dimension", $("#bi-dim").value);
  if ($("#bi-report").value) p.set("report_id", $("#bi-report").value);
  if ($("#bi-wg").value) p.set("working_group", $("#bi-wg").value);
  if ($("#bi-status").value) p.set("match_status", $("#bi-status").value);
  const d = await api("/api/bi/query?" + p.toString());
  BI_RESULT = { ...d, source: "/api/bi/query?" + p.toString(), source_generated_at: new Date().toISOString() };
  renderBI(BI_RESULT);
}
function renderBI(d) {
  $("#bi-desc").textContent = d.description?.en || "";
  const label = d.label?.en || d.metric;
  let head, body;
  if (d.dimension) {
    head = `<tr><th>${esc(d.dimension)}</th><th class="num">${esc(label)}</th></tr>`;
    body = d.rows.map((r) => `<tr><td>${esc(r.dim ?? "—")}</td>
      <td class="num">${valueLabel(r.value, d.unit || "")}</td></tr>`).join("");
  } else {
    head = `<tr><th>${esc(label)}</th></tr>`;
    body = d.rows.map((r) => `<tr><td class="num">${valueLabel(r.value, d.unit || "")}</td></tr>`).join("");
  }
  $("#bi-result").innerHTML =
    `<div id="bi-chart" class="chart" style="margin-bottom:12px"></div>
     <div class="tblwrap"><table class="tbl"><thead>${head}</thead><tbody>${body}</tbody></table></div>
     <h4>${t("ai.sql")}</h4><pre class="sql">${esc(d.sql)}</pre>`;
  const exportInfo = { title: label + (d.dimension ? " by " + d.dimension.replaceAll("_", " ") : ""),
    unit: d.unit || "Count", filters: d.filters, source: d.source, source_generated_at: d.source_generated_at,
    sql: d.sql, rows: d.rows, provenance: { metric: d.metric, dimension: d.dimension,
      axis_min: 0, ...(d.unit === "%" ? { axis_max: 100 } : {}) } };
  if (d.dimension && d.rows.length) {
    const shaped = d.rows.map((r) => ({ k: r.dim, v: r.value }));
    if (d.chart === "line") lineChart($("#bi-chart"), shaped.map((r) => ({ year: r.k, n: r.v })),
      { xKey: "year", yKey: "n", unit: d.unit || "", max: d.unit === "%" ? 100 : undefined, exportInfo });
    else barChart($("#bi-chart"), shaped, { labelKey: "k", valueKey: "v", unit: d.unit || "", max: d.unit === "%" ? 100 : undefined, exportInfo });
  } else {
    const host = $("#bi-chart"), width = Math.max(host.clientWidth || 460, 320);
    host.innerHTML = `<svg width="${width}" height="65" role="img"><text class="bar-val" x="8" y="42" style="font-size:30px">${esc(valueLabel(d.rows[0]?.value, d.unit || ""))}</text></svg>`;
    registerChart(host, d.rows, exportInfo);
  }
}

/* -------------------------------------------------------------- analytics */
async function loadAnalyticsIndex() {
  const rows = await api("/api/analysis");
  const seen = new Set(), cards = [];
  for (const r of rows) { if (!seen.has(r.analysis_type)) { seen.add(r.analysis_type); cards.push(r); } }
  $("#analytics-index").innerHTML =
    `<div class="tblwrap"><table class="tbl"><thead><tr><th>Analysis</th><th>Run</th>
      <th>Version</th><th></th></tr></thead><tbody>` +
    cards.map((c) => `<tr><td><code>${esc(c.analysis_type)}</code></td>
      <td>${esc(c.created_at)}</td><td>${esc(c.software_version)}</td>
      <td><button class="mini" data-analysis="${esc(c.analysis_type)}">open</button></td></tr>`)
      .join("") + "</tbody></table></div>";
  $$("#analytics-index button[data-analysis]").forEach((b) => b.addEventListener("click",
    () => openAnalysis(b.dataset.analysis)));
}
async function openAnalysis(type) {
  const d = await api(`/api/analysis/${encodeURIComponent(type)}`);
  $("#analysis-title").textContent = type;
  const out = d.output;
  let rendered;
  if (Array.isArray(out)) {
    const cols = out.length ? Object.keys(out[0]) : [];
    rendered = `<div class="tblwrap"><table class="tbl"><thead><tr>${
      cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>` +
      out.slice(0, 100).map((r) => `<tr>${cols.map((c) =>
        `<td>${typeof r[c] === "object" ? esc(JSON.stringify(r[c]).slice(0, 90)) : esc(r[c] ?? "—")}</td>`)
        .join("")}</tr>`).join("") + "</tbody></table></div>";
  } else {
    rendered = `<pre class="sql">${esc(JSON.stringify(out, null, 2).slice(0, 8000))}</pre>`;
  }
  $("#analysis-body").innerHTML = rendered +
    `<h4>${t("ai.sql")}</h4><pre class="sql">${esc(d.query || "—")}</pre>
     <h4>Parameters</h4><pre class="sql">${esc(JSON.stringify(d.parameters, null, 2))}</pre>`;
  $("#card-analysis").classList.remove("hidden");
  $("#card-analysis").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* --------------------------------------------------------------- insights */
async function loadInsights() {
  const rows = await api(`/api/insights?lang=${encodeURIComponent(LOCALE)}`);
  $("#insight-feed").innerHTML = rows.length ? rows.map((r) => `
    <div class="insight ${esc(r.severity)}">
      <div class="bar"></div>
      <div>
        <div class="t">${esc(r.title)}</div>
        <div class="b">${esc(r.body)}</div>
        <details><summary>${t("table.detail")} · ${esc(r.category)}</summary>
          <pre class="sql">${esc(JSON.stringify(r.evidence, null, 2).slice(0, 3000))}</pre>
          ${r.link ? `<div style="margin-top:6px"><a href="${esc(r.link)}">→ ${esc(r.link)}</a></div>` : ""}
        </details>
      </div>
    </div>`).join("") : `<div class="empty">${esc(t("state.empty"))}</div>`;
}

/* --------------------------------------------------------------- networks */
let NET = null, NET_KIND = "chapter-chapter", NET_SOURCE = "", NET_GENERATED = "";
function networkCaption(data) {
  const parameters = data.parameters || {}, parts = [
    `${nf(data.node_count)} displayed nodes`, `${nf(data.edge_count)} displayed edges`,
  ];
  if (parameters.min_weight !== undefined) parts.push(`minimum overlap ${parameters.min_weight}`);
  if (parameters.min_chapters !== undefined) parts.push(`at least ${parameters.min_chapters} chapters per journal`);
  if (parameters.min_coauthored_papers !== undefined) parts.push(`at least ${parameters.min_coauthored_papers} coauthored papers`);
  if (parameters.min_chapters_per_paper !== undefined) parts.push(`at least ${parameters.min_chapters_per_paper} chapters per paper`);
  if (parameters.eligible_edge_count !== undefined) parts.push(`${nf(parameters.eligible_edge_count)} eligible edges`);
  if (parameters.limit !== undefined) parts.push(`edge limit ${nf(parameters.limit)}`);
  return parts.join(" · ") + (parameters.truncated
    ? `. Truncated view: ${nf(parameters.omitted_edge_count)} eligible edges omitted.` : ".");
}
async function loadNetwork(reset) {
  const meta = await api("/api/networks");
  $("#net-kind").innerHTML = meta.kinds.map((k) =>
    `<option value="${esc(k.key)}" data-i18n="net.${esc(k.key)}">${esc(t("net." + k.key))}</option>`).join("");
  $("#net-kind").value = NET_KIND;
  if (reset) $("#net-threshold").value = meta.defaults[NET_KIND] ?? 0.25;
  $("#net-threshold-val").textContent = Number($("#net-threshold").value).toFixed(2);
  $("#net-kind").onchange = () => {
    NET_KIND = $("#net-kind").value; loadNetwork(true);
  };
  $("#net-threshold").oninput = () => {
    $("#net-threshold-val").textContent = Number($("#net-threshold").value).toFixed(2);
  };
  $("#net-threshold").onchange = () => loadNetwork(false);
  $("#net-fit").onclick = () => renderNetwork();
  const mw = NET_KIND.endsWith("chapter") || NET_KIND === "report-report"
    ? Number($("#net-threshold").value) : "";
  NET_SOURCE = `/api/networks/${NET_KIND}` + (mw === "" ? "" : `?min_weight=${mw}`);
  NET = await api(NET_SOURCE); NET_GENERATED = new Date().toISOString();
  $("#net-stats").textContent = networkCaption(NET);
  renderNetwork();
  $("#net-export-png").disabled = !NET.nodes.length;
  $("#net-export-json").disabled = false;
}
let CY = null;   // cytoscape instance

/* Cytoscape.js renders the network (MIT, vendored). The hand-written SVG force
   layout below remains as a fallback if the library fails to load. */
function renderNetwork() {
  if (!NET) return;
  if (window.cytoscape) return renderNetworkCytoscape();
  return renderNetworkSVG();
}

/* Cytoscape's colour parser does not accept hsl() strings; it silently falls back
   to grey. Convert to hex before handing the palette over. */
function hueToHex(h, sat, lig) {
  const a = sat * Math.min(lig, 1 - lig);
  const f = (n) => {
    const k = (n + h / 30) % 12;
    const c = lig - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(255 * c).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}
function groupColour(group, dark) {
  const s = String(group ?? "x");
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return hueToHex(h, 0.52, dark ? 0.60 : 0.46);
}

function renderNetworkCytoscape() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const maxW = Math.max(...NET.edges.map((e) => e.weight || 0), 1);
  // label only the better-connected nodes, otherwise dense clusters overprint
  const degree = {};
  NET.edges.forEach((e) => {
    degree[e.source] = (degree[e.source] || 0) + 1;
    degree[e.target] = (degree[e.target] || 0) + 1;
  });
  const budget = NET.nodes.length <= 24 ? NET.nodes.length
               : Math.min(16, Math.max(10, Math.round(NET.nodes.length * 0.22)));
  const labelled = new Set(NET.nodes
    .slice()
    .sort((a, b) => ((degree[b.id] || 0) - (degree[a.id] || 0))
                  || ((b.size || 0) - (a.size || 0)))
    .slice(0, budget)
    .map((n) => String(n.id)));
  const elements = [
    ...NET.nodes.map((n) => ({ data: {
        id: String(n.id), label: labelled.has(String(n.id)) ? String(n.label || n.id) : "",
        size: n.size || 1, group: String(n.group ?? "x"), colour: groupColour(n.group, dark),
        hint: `${n.label}${n.size ? " · " + n.size : ""}` } })),
    ...NET.edges.map((e, i) => ({ data: {
        id: "e" + i, source: String(e.source), target: String(e.target),
        weight: e.weight ?? 1,
        hint: `${e.source} — ${e.target}${e.weight !== undefined ? " · " + e.weight : ""}` } })),
  ];
  if (CY) CY.destroy();
  CY = window.cytoscape({
    container: $("#net-svg"),
    elements,
    style: [
      { selector: "node", style: {
          "background-color": "data(colour)",
          "width": "mapData(size,0,200,8,34)", "height": "mapData(size,0,200,8,34)",
          "label": "data(label)", "font-size": 8.5,
          "color": dark ? "#c3cedb" : "#33414f",
          "text-valign": "center", "text-halign": "right", "text-margin-x": 4,
          "text-max-width": 110, "text-wrap": "ellipsis",
          "border-width": 1, "border-color": dark ? "#161b22" : "#ffffff" } },
      { selector: "edge", style: {
          "width": "mapData(weight,0," + maxW + ",0.5,3.5)", "line-color": css("--muted"),
          "curve-style": "haystack", "opacity": 0.35 } },
      { selector: ":selected", style: { "background-color": css("--accent"), "opacity": 1 } },
    ],
    layout: { name: "cose", nodeDimensionsIncludeLabels: true,
              animate: false, padding: 26, nodeRepulsion: 11000, idealEdgeLength: 78,
              nodeOverlap: 24, componentSpacing: 60 },
    wheelSensitivity: 0.2,
    minZoom: 0.15, maxZoom: 4,
  });
  CY.ready(() => { CY.fit(undefined, 34); });     // fill the canvas instead of leaving margins
  CY.on("layoutstop", () => CY.fit(undefined, 34));
  const tt = $("#net-tip");
  CY.on("mouseover", "node, edge", (ev) => {
    const d = ev.target.data();
    tt.textContent = d.hint || d.label || d.id;
    tt.style.display = "block";
    const r = $("#net-wrap").getBoundingClientRect();
    tt.style.left = (ev.originalEvent.clientX - r.left + 12) + "px";
    tt.style.top = (ev.originalEvent.clientY - r.top + 12) + "px";
  });
  CY.on("mouseout", () => (tt.style.display = "none"));
  CY.on("tap", "node", (ev) => {                 // click a node to focus its neighbourhood
    const n = ev.target;
    CY.elements().removeClass("faded");
    const keep = n.closedNeighborhood();
    CY.elements().not(keep).addClass("faded");
    CY.style().selector(".faded").style({ opacity: 0.12 }).update();
  });
  $("#net-fit").onclick = () => CY.fit(undefined, 30);
  document.documentElement.dataset.cy = String(NET.node_count);
}

function renderNetworkSVG() {
  if (!NET) return;
  const host = $("#net-svg"), W = 1200, H = 620;
  const nodes = NET.nodes.map((n, i) => ({ ...n, x: W / 2 + (Math.cos(i) * W) / 3,
                                               y: H / 2 + (Math.sin(i) * H) / 3,
                                               vx: 0, vy: 0 }));
  const idx = {}; nodes.forEach((n, i) => (idx[n.id] = i));
  const edges = NET.edges.filter((e) => idx[e.source] !== undefined && idx[e.target] !== undefined);
  // deterministic force-directed layout (Fruchterman-Reingold, fixed iterations)
  const k = Math.sqrt((W * H) / Math.max(nodes.length, 1)) * 0.55;
  for (let it = 0; it < 220; it++) {
    const temp = Math.max(0.4, 22 * (1 - it / 220));
    for (const n of nodes) { n.vx = 0; n.vy = 0; }
    for (let a = 0; a < nodes.length; a++) for (let b = a + 1; b < nodes.length; b++) {
      let dx = nodes[a].x - nodes[b].x, dy = nodes[a].y - nodes[b].y;
      let d2 = dx * dx + dy * dy || 1, d = Math.sqrt(d2);
      const f = (k * k) / d2;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      nodes[a].vx += fx; nodes[a].vy += fy; nodes[b].vx -= fx; nodes[b].vy -= fy;
    }
    for (const e of edges) {
      const a = nodes[idx[e.source]], b = nodes[idx[e.target]];
      let dx = a.x - b.x, dy = a.y - b.y, d = Math.hypot(dx, dy) || 1;
      const f = (d * d) / k * (0.25 + (e.weight || 1) * 0.2);
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
    }
    for (const n of nodes) {
      const d = Math.hypot(n.vx, n.vy) || 1;
      n.x += (n.vx / d) * Math.min(d, temp) * 0.5;
      n.y += (n.vy / d) * Math.min(d, temp) * 0.5;
      n.x = Math.max(30, Math.min(W - 30, n.x));
      n.y = Math.max(20, Math.min(H - 20, n.y));
    }
  }
  // Label only the most connected nodes so dense graphs stay readable.
  const degree = {};
  edges.forEach((e) => { degree[e.source] = (degree[e.source] || 0) + 1;
                         degree[e.target] = (degree[e.target] || 0) + 1; });
  const labelBudget = nodes.length <= 40 ? nodes.length : Math.max(24, Math.round(nodes.length * 0.45));
  const labelled = new Set(nodes
    .slice()
    .sort((a, b) => ((degree[b.id] || 0) - (degree[a.id] || 0)) || ((b.size || 0) - (a.size || 0)))
    .slice(0, labelBudget)
    .map((n) => n.id));
  const maxW = Math.max(...edges.map((e) => e.weight || 0), 1);
  const hue = (g) => { let h = 0; const s = String(g || "x");
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; };
  let out = "";
  for (const e of edges) {
    const a = nodes[idx[e.source]], b = nodes[idx[e.target]];
    const w = 0.4 + 2.4 * ((e.weight || 1) / maxW);
    out += `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}"
      y2="${b.y.toFixed(1)}" stroke="${css("--muted")}" stroke-opacity="0.35"
      stroke-width="${w.toFixed(2)}" data-tip="${esc(a.label)} ↔ ${esc(b.label)}${
      e.weight !== undefined ? " · " + e.weight : ""}"/>`;
  }
  for (const n of nodes) {
    const r = Math.max(3, Math.min(15, 3 + Math.sqrt(n.size || 1)));
    out += `<circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${r.toFixed(1)}"
      fill="hsl(${hue(n.group)} 62% ${document.documentElement.getAttribute("data-theme") === "dark" ? 58 : 42}%)"
      fill-opacity="0.85" stroke="${css("--panel")}" stroke-width="1"
      data-tip="${esc(n.label)}${n.size ? " · " + n.size : ""}"/>`;
    if (labelled.has(n.id))
      out += `<text class="nodelabel" x="${(n.x + r + 2).toFixed(1)}" y="${(n.y + 3).toFixed(1)}">${
        esc(String(n.label).slice(0, 26))}</text>`;
  }
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${out}</svg>`;
  $$("#net-svg circle, #net-svg line").forEach((el) => {
    el.addEventListener("mouseenter", (e) => {
      const tt = $("#net-tip"); tt.textContent = el.getAttribute("data-tip");
      tt.style.display = "block";
      const rect = $("#net-wrap").getBoundingClientRect();
      tt.style.left = (e.clientX - rect.left + 10) + "px";
      tt.style.top = (e.clientY - rect.top + 10) + "px";
    });
    el.addEventListener("mouseleave", () => ($("#net-tip").style.display = "none"));
  });
}

/* ---------------------------------------------------------------- quality */
async function loadQuality() {
  const q = await api("/api/data-quality");
  const s = q.summary;
  const comp = STATS ? STATS.completeness : null;
  if (comp) {
    const c = (comp.doi + comp.title + comp.author + comp.journal + comp.year) / 5;
    const score = Math.max(0, c - 0.5 * (100 * (s.manual_review_required || 0) /
      Math.max(STATS.totals.references, 1)));
    $("#quality-score").innerHTML =
      `<b>${t("quality.score")}: ${score.toFixed(1)} / 100</b><br>${t("quality.score.formula")}`;
  }
  const keys = ["missing_doi", "missing_title", "missing_year", "missing_journal",
                "missing_author", "invalid_doi", "possible_duplicate_title",
                "suspicious_year", "manual_review_required"];
  $("#quality-cards").innerHTML = keys.map((k) => {
    const v = s[k] ?? 0;
    return `<div class="kpi clickable ${v > 0 ? "warn" : "ok"}" data-flag="${esc(k)}">
      <div class="v">${nf(v)}</div><div class="l">${esc(t("quality." + k))}</div></div>`;
  }).join("");
  $("#tbl-flags").innerHTML =
    `<thead><tr><th>${t("table.flag")}</th><th>${t("table.severity")}</th>
      <th class="num">${t("table.count")}</th><th></th></tr></thead><tbody>` +
    q.by_type.map((f) => `<tr><td><code>${esc(f.flag_type)}</code></td>
      <td><span class="chip ${f.severity === "high" ? "bad" : f.severity === "medium" ? "warn" : ""}">${esc(f.severity)}</span></td>
      <td class="num">${nf(f.n)}</td>
      <td><button class="mini" data-flag="${esc(f.flag_type)}">${t("btn.show_records")}</button></td>
    </tr>`).join("") + "</tbody>";
  $$("#quality-cards .kpi[data-flag], #tbl-flags button[data-flag]").forEach((el) =>
    el.addEventListener("click", () => loadFlagRecords(el.dataset.flag, 1)));
  const dup = await api("/api/duplicates?limit=25");
  $("#dup-title").textContent = `${t("quality.possible_duplicate_title")} (${nf(dup.total_groups)})`;
  $("#dup-hint").textContent =
    "Distinct paper records with the same normalized title. Review their metadata to identify duplicates.";
  $("#dup-list").innerHTML = dup.groups.length ? dup.groups.map((g) => `
    <details style="margin-bottom:7px"><summary style="cursor:pointer">${esc(g.title_norm.slice(0, 108))}
      <span class="chip warn">${g.n}</span></summary>
      <div class="tblwrap"><table class="tbl"><thead><tr><th>ID</th><th>${t("table.title")}</th>
        <th>${t("table.year")}</th><th>${t("table.journal")}</th><th>${t("table.doi")}</th>
        <th class="num">${t("table.occurrences")}</th><th>${t("table.status")}</th></tr></thead><tbody>` +
      g.papers.map((p) => `<tr><td>${p.paper_id}</td><td>${esc(p.title_best || "")}</td>
        <td>${esc(p.year_best || "—")}</td><td>${esc((p.journal_best || "—").slice(0, 38))}</td>
        <td>${esc(p.doi_norm || "—")}</td><td class="num">${nf(p.n_occurrences)}</td>
        <td>${verifChip(p.verification_status)}</td></tr>`).join("") +
      "</tbody></table></div></details>").join("")
    : `<div class="empty">${esc(t("state.empty"))}</div>`;
}
async function loadFlagRecords(flag, page = 1) {
  const d = await api(`/api/data-quality/records?flag_type=${encodeURIComponent(flag)}&page=${page}&size=25`);
  $("#flag-records-title").textContent = `${flag} — ${nf(d.total)}`;
  $("#tbl-flag-records").innerHTML =
    `<thead><tr><th>${t("table.entity")}</th><th>${t("table.severity")}</th><th>${t("table.detail")}</th>
      <th>${t("table.chapter")}</th><th>${t("table.parsed_title")}</th><th>${t("table.year")}</th>
      <th>${t("table.doi")}</th></tr></thead><tbody>` +
    (d.items.length ? d.items.map((r) => `<tr>
      <td>${esc(r.entity_type)}<br><span class="hint">${esc(r.entity_id)}</span></td>
      <td><span class="chip ${r.severity === "high" ? "bad" : r.severity === "medium" ? "warn" : ""}">${esc(r.severity)}</span></td>
      <td>${esc((r.detail || "").slice(0, 70))}</td><td>${esc(r.chapter_id || "—")}</td>
      <td>${esc((r.parsed_title || "—").slice(0, 64))}</td><td>${esc(r.parsed_year || "—")}</td>
      <td>${esc(r.doi_norm || "—")}</td></tr>`).join("")
      : `<tr><td colspan="7"><div class="empty">${esc(t("state.empty"))}</div></td></tr>`) + "</tbody>";
  $("#pager-flag-records").innerHTML = pagerHTML(d);
  bindPager("#pager-flag-records", (p) => loadFlagRecords(flag, p));
  $("#card-flag-records").classList.remove("hidden");
  $("#card-flag-records").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ------------------------------------------------------------- unresolved */
async function loadUnresolved(page = 1) {
  await loadFilterOptions();
  const p = new URLSearchParams({ page, size: 25 });
  if ($("#u-report").value) p.set("report_id", $("#u-report").value);
  if ($("#u-dionly").checked) p.set("only_doi_less", "true");
  const d = await api("/api/unresolved?" + p.toString());
  UNRESOLVED_QUERY = p.toString();
  $("#u-summary").textContent = `${nf(d.total)} — page ${d.page} / ${Math.max(1, Math.ceil(d.total / d.size))}`;
  $("#tbl-unresolved").innerHTML =
    `<thead><tr><th>${t("table.report")} / ${t("table.wg")} / ${t("table.chapter")}</th>
      <th>${t("table.page")}</th><th>${t("table.original")}</th><th>${t("table.doi")}</th>
      <th>${t("table.review")}</th></tr></thead><tbody>` +
    (d.items.length ? d.items.map((r) => `<tr>
      <td>${esc(r.report_id)} ${esc(r.wg_label)}<br><span class="hint">${esc(r.chapter_label)}</span></td>
      <td>${esc(r.source_page ?? "—")}</td>
      <td title="${esc(r.raw_reference || "")}">${esc((r.raw_reference || "").slice(0, 150))}</td>
      <td>${r.doi_norm ? esc(r.doi_norm) : `<span class="chip warn">no DOI</span>`}</td>
      <td><span class="chip ${r.review_status === "not_reviewed" ? "" : "info"}">${
        esc(r.review_status === "not_reviewed" ? t("state.not_reviewed") : r.review_status)}</span>
        <button class="mini" data-review="${esc(r.occurrence_id)}">${t("btn.review")}</button></td>
    </tr>`).join("") : `<tr><td colspan="5"><div class="empty">${esc(t("state.empty"))}</div></td></tr>`)
    + "</tbody>";
  $("#pager-unresolved").innerHTML = pagerHTML(d);
  bindPager("#pager-unresolved", loadUnresolved);
  $$("#tbl-unresolved button[data-review]").forEach((b) =>
    b.addEventListener("click", () => reviewDialog(b.dataset.review)));
}
$("#btn-u-search").addEventListener("click", () => loadUnresolved(1));

function reviewDialog(oid) {
  openModal(`<h2>${t("table.review")}</h2>
    <p class="hint"><code>${esc(oid)}</code></p>
    <div class="filters" style="flex-direction:column;align-items:stretch">
      <select id="rv-status">
        <option value="in_progress">In progress</option><option value="resolved">Resolved</option>
        <option value="wont_fix">Closed without changes</option><option value="open">Open</option></select>
      <input id="rv-assignee" placeholder="Assigned to">
      <input id="rv-note" placeholder="Note">
      <input id="rv-reason" placeholder="Reason (required)">
      <button class="primary" id="rv-save">${t("btn.save")}</button>
      <div id="rv-msg" class="hint"></div>
    </div>`);
  $("#rv-save").addEventListener("click", async () => {
    const body = { status: $("#rv-status").value, assigned_to: $("#rv-assignee").value || null,
                   note: $("#rv-note").value, reason: $("#rv-reason").value };
    if (body.reason.trim().length < 3) { $("#rv-msg").textContent = "Enter a reason of at least 3 characters."; return; }
    try {
      const r = await fetch(`/api/references/${encodeURIComponent(oid)}/resolve`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(await r.text());
      $("#rv-msg").textContent = "Review saved."; loadUnresolved(1);
    } catch (e) { $("#rv-msg").textContent = "Could not save the review: " + e.message; }
  });
}

/* ---------------------------------------------------------------- progress */
async function loadProgress() {
  const d = await api("/api/collection-progress");
  $("#progress-note").textContent = d.note;
  $("#tbl-progress").innerHTML =
    `<thead><tr><th>${t("table.report")}</th><th>${t("table.wg")}</th><th>${t("table.chapter")}</th>
      <th>${t("table.status")}</th><th class="num">${t("table.refs")}</th>
      <th class="num">${t("table.matched")}</th><th class="num">${t("table.unmatched")}</th>
      <th class="num">${t("table.coverage")}</th></tr></thead><tbody>` +
    d.chapters.filter((c) => c.n_references > 0).map((c) => `<tr>
      <td>${esc(c.report_id)}</td><td>${esc(c.wg_label)}</td>
      <td>${esc(c.chapter_label)} — ${esc((c.chapter_title || "").slice(0, 52))}</td>
      <td><span class="chip">${esc(c.extraction_status || "—")}</span></td>
      <td class="num">${nf(c.n_references)}</td><td class="num">${nf(c.matched)}</td>
      <td class="num">${nf(c.unresolved)}</td>
      <td class="num">${progressBar(100 * c.matched / Math.max(c.n_references, 1))}</td>
    </tr>`).join("") + "</tbody>";
}

/* --------------------------------------------------------------------- AI */
async function loadAIStatus() {
  const st = await api("/api/ai/status");
  $("#ai-status").innerHTML = st.configured
    ? `<div class="notice" style="background:var(--info-bg);border-color:var(--accent);color:var(--info-fg)">
         ${esc(st.provider)} · ${esc(st.model)}</div>`
    : `<div class="notice"><b>${esc(t("ai.not_configured"))}</b><br>${esc(t("ai.not_configured_hint"))}</div>`;
  $("#ai-controls").classList.toggle("hidden", !st.configured);
  $("#ai-ask").onclick = askAI;
}
async function askAI() {
  const q = $("#ai-q").value.trim();
  if (q.length < 3) return;
  $("#ai-out").innerHTML = `<div class="empty">${esc(t("state.loading"))}</div>`;
  try {
    const d = await (await fetch("/api/ai/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, lang: LOCALE }) })).json();
    let html = "";
    if (d.error) html += `<div class="error">${esc(d.error)}</div>`;
    if (d.answer) html += `<h4>${t("ai.answer")}</h4><div class="abstract" style="max-height:none">${esc(d.answer)}</div>`;
    if (d.sql) html += `<h4>${t("ai.sql")}</h4><pre class="sql">${esc(d.sql)}</pre>`;
    if (d.rows?.length) {
      const cols = Object.keys(d.rows[0]);
      html += `<h4>${t("ai.rows")} (${nf(d.row_count)})</h4><div class="tblwrap"><table class="tbl">
        <thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>` +
        d.rows.slice(0, 30).map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c] ?? "—")}</td>`).join("")}</tr>`).join("") +
        "</tbody></table></div>";
    }
    $("#ai-out").innerHTML = html || `<div class="empty">${esc(t("state.empty"))}</div>`;
  } catch (e) {
    $("#ai-out").innerHTML = `<div class="error">${esc(t("state.error"))}: ${esc(e.message)}</div>`;
  }
}

/* --------------------------------------------------------------- settings */
async function loadSettings() {
  const [st, runs] = await Promise.all([api("/api/ai/status"), api("/api/analysis/run")]);
  $("#set-ai").textContent = JSON.stringify(st, null, 2);
  $("#set-runs").innerHTML = runs.length
    ? runs.slice(0, 5).map((r) => `<div>run ${r.run_id} · ${esc(r.start_time)} → ${esc(r.end_time)} · ` +
        `${esc(r.analysis_version)} · errors ${r.errors}</div>`).join("")
    : `<span class="hint">${esc(t("state.empty"))}</span>`;
  const s = STATS || await api("/api/statistics");
  $("#set-about").innerHTML =
    `${nf(s.totals.references)} ${t("kpi.total_references")} · ${nf(s.totals.papers)} ${t("kpi.total_papers")}<br>
     ${nf(s.totals.reports)} ${t("kpi.reports")} · ${nf(s.totals.chapters)} ${t("kpi.chapters")}<br>
     <span class="hint">${esc(t("quality.score.formula"))}</span>`;
}

/* ------------------------------------------------------------ paper detail */
document.addEventListener("click", async (e) => {
  const row = e.target.closest(".rowlink"); if (!row) return;
  try {
    const [d, cites] = await Promise.all([
      api(`/api/papers/${row.dataset.paper}`), api(`/api/papers/${row.dataset.paper}/citations`)]);
    const p = d.paper, w = d.wos_record || {};
    const abstract = w.Abstract || "";
    openModal(`
      <h2>${esc(p.title_best || "—")}</h2>
      <div class="hint">paper_id ${p.paper_id} · ${verifChip(p.verification_status)}</div>
      <div class="kv">
        <div class="k">${t("table.authors")}</div><div>${esc(p.authors_best || "—")}</div>
        <div class="k">${t("table.journal")}</div><div>${esc(p.journal_best || "—")}</div>
        <div class="k">${t("table.year")}</div><div>${esc(p.year_best || "—")}</div>
        <div class="k">${t("table.doi")}</div><div>${p.doi_norm
          ? `<a href="https://doi.org/${esc(p.doi_norm)}" target="_blank" rel="noopener">${esc(p.doi_norm)}</a>` : "—"}</div>
        <div class="k">metadata</div><div>${esc(p.metadata_source || "—")} (${esc(p.identity_method || "—")})</div>
        <div class="k">${t("table.occurrences")}</div><div>${nf(p.n_occurrences)} → ${nf(p.n_chapters)} ${t("table.chapters")} / ${nf(p.n_reports)} ${t("table.reports")}</div>
        <div class="k">WoS</div><div>${esc(p.wos_record_id || "—")}</div>
        <div class="k">${t("table.authors")}</div><div>${d.authors.map((a) => esc(a.full_name || a.surname)).join("; ") || "—"}</div>
      </div>
      ${abstract ? `<h4>Abstract (Web of Science)</h4><div class="abstract">${esc(abstract)}</div>` : ""}
      <h4>Cited in IPCC (${cites.length})</h4>
      <div class="tblwrap"><table class="tbl"><thead><tr><th>${t("table.report")}</th><th>${t("table.wg")}</th>
        <th>${t("table.chapter")}</th><th>${t("table.page")}</th><th>${t("table.match_status")}</th>
        <th>${t("table.original")}</th></tr></thead><tbody>
        ${cites.map((c) => `<tr><td>${esc(c.report_id)}</td><td>${esc(c.wg_label)}</td>
          <td>${esc(c.chapter_label)} — ${esc((c.chapter_title || "").slice(0, 42))}</td>
          <td>${esc(c.source_page ?? "—")}</td><td>${statusChip(c.match_status)}</td>
          <td title="${esc(c.raw_reference || "")}">${esc((c.raw_reference || "").slice(0, 88))}</td>
        </tr>`).join("")}</tbody></table></div>`);
  } catch (err) { fail(err); }
});

/* ---------------------------------------------------------------- exports */
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-export]"); if (!b) return;
  const [kind, fmt] = b.dataset.export.split(".");
  const p = new URLSearchParams();
  if (kind === "papers") {
    const q = PAPER_QUERY || litQuery(1);
    new URLSearchParams(q).forEach((v, k) => ["page", "size"].includes(k) || p.set(k === "wg" ? "working_group" : k, v));
  } else {
    const q = new URLSearchParams(UNRESOLVED_QUERY || "");
    q.forEach((v, k) => ["page", "size"].includes(k) || p.set(k, v));
    if (!UNRESOLVED_QUERY) {
      if ($("#u-report").value) p.set("report_id", $("#u-report").value);
      if ($("#u-dionly").checked) p.set("only_doi_less", "true");
    }
  }
  p.set("limit", "5000");
  const notice = $(kind === "papers" ? "#lit-export-status" : "#u-export-status");
  b.disabled = true; notice.textContent = t("state.loading");
  try {
    const response = await fetch(`/api/export/${kind}.${fmt}?` + p.toString());
    if (!response.ok) throw new Error(await response.text());
    const count = response.headers.get("X-Row-Count"), total = response.headers.get("X-Total-Count");
    const filename = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || `ipcc_${kind}.${fmt}`;
    downloadBlob(await response.blob(), filename);
    notice.textContent = response.headers.get("X-Export-Truncated") === "true"
      ? `Downloaded ${nf(count)} of ${nf(total)} matching records (download limit: 5,000).`
      : `Downloaded ${nf(count)} matching records.`;
  } catch (error) { notice.textContent = `${t("state.error")}: ${error.message}`; }
  finally { b.disabled = false; }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-network-download]"); if (!button || !NET) return;
  const format = button.dataset.networkDownload, kind = NET.parameters.kind || NET_KIND;
  button.disabled = true;
  try {
    const provenance = { ...NET.provenance, dataset: "IPCC Literature Database", generated_at: new Date().toISOString(),
      source_generated_at: NET_GENERATED, source: NET_SOURCE, parameters: NET.parameters,
      view: { zoom: CY?.zoom(), pan: CY?.pan(), highlighted_nodes: CY?.nodes().filter((n) => !n.hasClass("faded")).map((n) => n.id()) } };
    if (format === "json") {
      const data = { ...NET, provenance,
        positions: CY ? Object.fromEntries(CY.nodes().map((node) => [node.id(), node.position()])) : {} };
      downloadBlob(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), `ipcc_network_${kind}.json`);
    } else if (CY) {
      const image = new Image(); image.src = CY.png({ full: false, scale: 2, bg: css("--panel2") });
      await image.decode();
      const canvas = document.createElement("canvas"); canvas.width = image.naturalWidth;
      let context = canvas.getContext("2d");
      const wrap = (text, font) => {
        context.font = font;
        const lines = []; let line = "";
        for (const word of text.split(/\s+/)) {
          if (line && context.measureText(line + " " + word).width > canvas.width - 48) { lines.push(line); line = ""; }
          line += (line ? " " : "") + word;
        }
        if (line) lines.push(line);
        return lines;
      };
      const title = wrap(t("net." + kind), "bold 22px Arial"), caption = wrap(networkCaption(NET), "17px Arial");
      const footer = wrap(`IPCC Literature Database · Retrieved ${NET_GENERATED}`, "15px Arial");
      const headerHeight = 32 + title.length * 28 + caption.length * 23 + footer.length * 21;
      canvas.height = image.naturalHeight + headerHeight; context = canvas.getContext("2d");
      context.fillStyle = css("--panel"); context.fillRect(0, 0, canvas.width, canvas.height);
      let y = 31;
      for (const [lines, font, step, color] of [[title, "bold 22px Arial", 28, css("--fg")],
        [caption, "17px Arial", 23, css("--fg2")], [footer, "15px Arial", 21, css("--muted")]]) {
        context.font = font; context.fillStyle = color;
        for (const line of lines) { context.fillText(line, 24, y); y += step; }
      }
      context.drawImage(image, 0, headerHeight);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      downloadBlob(blob, `ipcc_network_${kind}.png`);
    } else {
      const entry = { title: t("net." + kind), unit: networkCaption(NET),
        filters: {}, source: NET_SOURCE, source_generated_at: NET_GENERATED, rows: NET.nodes, provenance };
      downloadBlob(await svgToPng(standaloneSvg($("#net-svg svg"), entry)), `ipcc_network_${kind}.png`);
    }
  } catch (error) { fail(error); }
  finally { button.disabled = false; }
});

/* ------------------------------------------------------------------- boot */
(async function boot() {
  $("#theme").onchange = (e) => applyTheme(e.target.value);
  $("#set-theme").onchange = (e) => applyTheme(e.target.value);
  const savedTheme = localStorage.getItem("ipcc.theme") || "system";
  applyTheme(savedTheme, false);
  try { await loadEnglish(); } catch (e) { fail(e); return; }
  const hash = (location.hash || "#/overview").replace("#/", "") || "overview";
  if ($(`#tabs button[data-view="${hash}"]`)) show(hash); else show("overview");
  try { clearFail(); await loadOverview(); } catch (e) { fail(e); }
})();
