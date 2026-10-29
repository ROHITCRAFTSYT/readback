"use strict";

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();
const pct = (x, d = 1) => (x == null ? "–" : `${(100 * x).toFixed(d)}%`);
const LEVELS = {
  1: "standard phraseology",
  2: "everyday variation",
  3: "real radio: cut-offs, hesitations, misheard words",
  4: "stress: every kind of noise at once",
};
let data = null;
let points = [];

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function ago(iso) {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${(s / 3600).toFixed(1)} h ago`;
}

function vs(id, mine, theirs, label) {
  const p = $(id);
  p.textContent = "";
  if (theirs == null) return;
  const diff = mine - theirs;
  p.append(`${label} `, el("b", null, pct(theirs)), " · ");
  p.append(el("span", diff >= 0 ? "up" : "down", `${diff >= 0 ? "+" : "−"}${(100 * Math.abs(diff)).toFixed(1)} pts`));
}

function renderStatus() {
  const st = data.status;
  const champ = st.champion;
  const rules = st.baseline.rules;
  const badge = $("state");
  badge.textContent = st.optimal ? "optimal" : "training";
  badge.className = `badge ${st.optimal ? "optimal" : "training"}`;
  $("round").textContent = st.round;
  $("level").textContent = st.level;
  $("level-name").textContent = `(${LEVELS[st.level] || ""})`;
  $("champ-round").textContent = champ.round;
  $("hours").textContent = (st.train_seconds / 3600).toFixed(1);
  $("examples").textContent = st.examples_seen.toLocaleString();
  $("updated").textContent = `updated ${ago(st.updated)}`;

  const t = champ.gold_test || {};
  const r = rules.gold_test || {};
  $("k-f1").textContent = pct(t.item_f1);
  vs("k-f1-vs", t.item_f1, r.item_f1, "rules");
  $("k-exact").textContent = pct(t.exact);
  vs("k-exact-vs", t.exact, r.exact, "rules");
  $("k-cs").textContent = pct(t.callsign);
  vs("k-cs-vs", t.callsign, r.callsign, "rules");
  renderBlind();
  $("k-syn-title").textContent = `Synthetic level ${st.level} · exactly right`;
  $("k-syn").textContent = pct(champ.val.exact);
  vs("k-syn-vs", champ.val.exact, (rules[`val_${st.level}`] || {}).exact, "rules");
}

const AIRPORTS = {
  LSGS: "Sion", LSZB: "Bern", LSZH: "Zurich", YSSY: "Sydney", LZIB: "Bratislava", LKPR: "Prague", LKTB: "Brno",
};

function renderBlind() {
  const st = data.status;
  const b = st.champion.blind;
  const info = st.blind;
  $("blind-panel").hidden = !(b && info);
  if (!b || !info) return;
  const r = st.baseline.rules.blind || {};
  const names = Object.keys(info.airports).map((a) => AIRPORTS[a] || a);
  $("blind-hint").textContent = `set 2: ${info.transmissions} ATCO2 transmissions from ${names.length} airports, never used for training or tuning`;
  $("b-f1").textContent = pct(b.item_f1);
  vs("b-f1-vs", b.item_f1, r.item_f1, "rules");
  $("b-exact").textContent = pct(b.exact);
  vs("b-exact-vs", b.exact, r.exact, "rules");
  $("b-cs").textContent = pct(b.callsign);
  vs("b-cs-vs", b.callsign, r.callsign, "rules");
  const note = $("blind-note");
  note.textContent = "";
  note.append(`${names.join(", ")}. Labeled by hand and frozen on ${info.frozen.slice(0, 10)}, before any parser saw them; only the hash is published. `);
  note.append(info.generator_unchanged
    ? el("span", "ok", "Generator unchanged since the freeze.")
    : el("span", "warn", "Generator changed since the freeze (see git history)."));
  const seen = st.champion.atco2_seen;
  if (seen) {
    const rs = st.baseline.rules.atco2_seen || {};
    note.append(el("br"), `ATCO2 set 1, retired (labeling it shaped generator v3, so it is no longer blind): item F1 `,
      el("b", null, pct(seen.item_f1)), ` vs rules ${pct(rs.item_f1)}.`);
  }
}

function renderLevels() {
  const st = data.status;
  const passed = Object.fromEntries(st.levels_passed.map((p) => [p.level, p]));
  const ol = $("levels");
  ol.textContent = "";
  for (const [n, name] of Object.entries(LEVELS)) {
    const li = el("li", passed[n] ? "done" : Number(n) === st.level ? "now" : "");
    li.append(el("span", "n", n), el("span", null, name));
    li.append(el("span", "when", passed[n] ? `passed · round ${passed[n].round}` : Number(n) === st.level ? "training" : ""));
    ol.append(li);
  }
  const chips = $("focus");
  chips.textContent = "";
  const focus = Object.entries(st.focus).sort((a, b) => b[1] - a[1]).slice(0, 10);
  if (!focus.length) chips.append(el("span", "empty", "No weak spot stands out."));
  for (const [f, w] of focus) {
    const c = el("span", "chip focus", f.replace(":", " · ").replaceAll("_", " "));
    c.append(el("b", null, `×${w}`));
    chips.append(c);
  }
}

function renderKinds() {
  const t = (data.status.champion.gold_test || {}).kind_f1 || {};
  const r = (data.status.baseline.rules.gold_test || {}).kind_f1 || {};
  const kinds = [...new Set([...Object.keys(t), ...Object.keys(r)])].sort((a, b) => (t[b] ?? 0) - (t[a] ?? 0));
  const legend = $("kind-legend");
  legend.textContent = "";
  for (const [name, color] of [["Tagger", "--series-1"], ["Rule-based parser", "--series-2"]]) {
    const s = el("span");
    const sw = el("i", "swatch box");
    sw.style.background = css(color);
    s.append(sw, name);
    legend.append(s);
  }
  const box = $("kinds");
  box.textContent = "";
  for (const k of kinds) {
    const row = el("div", "kind-row");
    row.append(el("span", "name", k.replaceAll("_", " ")));
    const bars = el("div", "bars");
    for (const [v, color] of [[t[k], "--series-1"], [r[k], "--series-2"]]) {
      const line = el("div", "bar-line");
      const track = el("div", "bar-track");
      const fill = el("div", "bar-fill");
      fill.style.width = `${100 * (v ?? 0)}%`;
      fill.style.background = css(color);
      track.append(fill);
      line.append(track, el("b", null, pct(v, 0)));
      bars.append(line);
    }
    row.append(bars);
    box.append(row);
  }
}

function renderTables() {
  const checks = $("checks");
  if (data.checks.length) {
    checks.textContent = "";
    for (const c of data.checks.slice(-10).reverse()) {
      const tr = el("tr");
      tr.append(el("td", null, c.seed), el("td", null, `r${c.champion_round ?? "?"}`),
        el("td", null, pct(c.synthetic.exact)), el("td", null, pct((c.gold_test || {}).item_f1)),
        el("td", null, pct((c.blind || {}).item_f1)));
      checks.append(tr);
    }
  }
  const rounds = $("rounds");
  rounds.textContent = "";
  for (const e of data.log.slice(-10).reverse()) {
    const tr = el("tr");
    tr.append(el("td", null, e.round), el("td", null, e.level), el("td", null, pct(e.candidate.val.exact)),
      el("td", null, pct((e.candidate.gold_dev || {}).item_f1)));
    const note = el("td");
    if (e.promoted) note.append(el("span", "tagp", "promoted "));
    if (e.event) note.append(el("span", "ev", e.event));
    tr.append(note);
    rounds.append(tr);
  }
}

// -- learning curve --------------------------------------------------------------------
const SERIES = [
  { key: "test", name: "Tagger · real radio F1", color: "--series-1" },
  { key: "rules", name: "Rule-based parser · real radio F1", color: "--series-2", dashed: true },
  { key: "val", name: "Tagger · synthetic exact (current level)", color: "--series-3" },
  { key: "cand", name: "Candidate tried that round", color: "--series-3", dot: true },
];

function renderLegend() {
  const legend = $("legend");
  legend.textContent = "";
  for (const s of SERIES) {
    const span = el("span");
    const sw = el("i", `swatch${s.dashed ? " dashed" : ""}${s.dot ? " dot" : ""}`);
    if (s.dot) sw.style.background = css(s.color);
    else sw.style.borderColor = css(s.color);
    span.append(sw, s.name);
    legend.append(span);
  }
}

function drawCurve() {
  const canvas = $("curve");
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (w < 50 || h < 50 || !data) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const log = data.log;
  const rulesF1 = (data.status.baseline.rules.gold_test || {}).item_f1;
  points = log.map((e) => ({
    round: e.round,
    level: e.level,
    test: e.champion ? e.champion.gold_test : null,
    val: e.champion ? e.champion.val : null,
    cand: e.candidate.val.exact,
    rules: rulesF1,
    promoted: e.promoted,
    event: e.event,
  }));
  if (!points.length) return;
  const vals = points.flatMap((p) => [p.test, p.val, p.cand, p.rules]).filter((v) => v != null);
  const lo = Math.max(0, Math.floor((Math.min(...vals) - 0.02) * 10) / 10);
  const pad = { l: 44, r: 16, t: 12, b: 26 };
  const x0 = points[0].round;
  const x1 = Math.max(points.at(-1).round, x0 + 1);
  const X = (r) => pad.l + ((r - x0) / (x1 - x0)) * (w - pad.l - pad.r);
  const Y = (v) => pad.t + (1 - (v - lo) / (1 - lo)) * (h - pad.t - pad.b);

  // level bands
  let start = 0;
  for (let i = 1; i <= points.length; i++) {
    if (i === points.length || points[i].level !== points[start].level) {
      const a = X(points[start].round) - (i - start === points.length ? 0 : 0);
      const b = i === points.length ? w - pad.r : X(points[i].round);
      ctx.fillStyle = points[start].level % 2 ? "rgba(75,240,160,0.035)" : "rgba(124,196,255,0.04)";
      ctx.fillRect(a, pad.t, b - a, h - pad.t - pad.b);
      ctx.fillStyle = css("--muted");
      ctx.font = "11px 'IBM Plex Mono', monospace";
      if (b - a > 40) ctx.fillText(`L${points[start].level}`, a + 6, pad.t + 14);
      start = i;
    }
  }
  // grid + axis labels
  ctx.strokeStyle = "#1a2432";
  ctx.lineWidth = 1;
  ctx.fillStyle = css("--muted");
  ctx.font = "11px 'IBM Plex Mono', monospace";
  for (let v = lo; v <= 1.0001; v += (1 - lo) / 4) {
    ctx.beginPath();
    ctx.moveTo(pad.l, Y(v));
    ctx.lineTo(w - pad.r, Y(v));
    ctx.stroke();
    ctx.fillText(`${Math.round(100 * v)}%`, 4, Y(v) + 4);
  }
  const step = Math.max(1, Math.ceil((x1 - x0) / 8));
  for (let r = x0; r <= x1; r += step) ctx.fillText(`r${r}`, X(r) - 8, h - 8);

  // candidates as dots, then lines on top
  ctx.fillStyle = css("--series-3");
  ctx.globalAlpha = 0.45;
  for (const p of points) {
    ctx.beginPath();
    ctx.arc(X(p.round), Y(p.cand), 3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
  const line = (key, color, dashed) => {
    ctx.strokeStyle = css(color);
    ctx.lineWidth = 2;
    ctx.setLineDash(dashed ? [6, 5] : []);
    ctx.beginPath();
    let on = false;
    for (const p of points) {
      if (p[key] == null) { on = false; continue; }
      if (on) ctx.lineTo(X(p.round), Y(p[key]));
      else ctx.moveTo(X(p.round), Y(p[key]));
      on = true;
    }
    ctx.stroke();
    ctx.setLineDash([]);
  };
  line("rules", "--series-2", true);
  line("val", "--series-3");
  line("test", "--series-1");
  // end-point markers, and a direct label on the headline line
  const last = points.at(-1);
  for (const [key, color] of [["rules", "--series-2"], ["val", "--series-3"], ["test", "--series-1"]]) {
    if (last[key] == null) continue;
    ctx.fillStyle = css(color);
    ctx.strokeStyle = css("--panel");
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(X(last.round), Y(last[key]), 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.font = "12px 'IBM Plex Mono', monospace";
  ctx.fillStyle = css("--text");
  if (last.test != null) {
    const label = pct(last.test);
    const tw = ctx.measureText(label).width;
    const lx = X(last.round) + 8 + tw < w - pad.r ? X(last.round) + 8 : X(last.round) - 8 - tw;
    ctx.fillText(label, lx, Y(last.test) - 8);
  }
  canvas._geom = { X, Y, pad, w, h };
}

function onHover(ev) {
  const canvas = $("curve");
  const tip = $("tip");
  if (!canvas._geom || !points.length) return;
  const rect = canvas.getBoundingClientRect();
  const mx = ev.clientX - rect.left;
  const { X } = canvas._geom;
  let best = points[0];
  for (const p of points) if (Math.abs(X(p.round) - mx) < Math.abs(X(best.round) - mx)) best = p;
  drawCurve();
  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "#3a4a5c";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(X(best.round), canvas._geom.pad.t);
  ctx.lineTo(X(best.round), canvas._geom.h - canvas._geom.pad.b);
  ctx.stroke();

  tip.textContent = "";
  tip.append(el("div", "t", `round ${best.round} · level ${best.level}${best.promoted ? " · promoted" : ""}`));
  for (const s of SERIES) {
    const r = el("div", "r");
    const name = el("span");
    const sw = el("i", `swatch${s.dashed ? " dashed" : ""}${s.dot ? " dot" : ""}`);
    if (s.dot) sw.style.background = css(s.color);
    else sw.style.borderColor = css(s.color);
    name.append(sw, s.name.split(" · ")[1] ? `${s.name.split(" · ")[0]}, ${s.name.split(" · ")[1]}` : s.name);
    r.append(name, el("b", null, pct(best[s.key])));
    tip.append(r);
  }
  if (best.event) tip.append(el("div", "t", best.event));
  tip.hidden = false;
  const left = Math.min(Math.max(8, X(best.round) + 12), rect.width - tip.offsetWidth - 8);
  tip.style.left = `${left}px`;
  tip.style.top = "12px";
}

async function refresh() {
  try {
    const res = await api("/api/training");
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    data = await res.json();
    renderStatus();
    renderLevels();
    renderKinds();
    renderTables();
    renderLegend();
    drawCurve();
  } catch (err) {
    $("state").textContent = "no run";
    $("updated").textContent = String(err.message || err);
  }
}

$("curve").addEventListener("pointermove", onHover);
$("curve").addEventListener("pointerleave", () => { $("tip").hidden = true; drawCurve(); });
new ResizeObserver(() => drawCurve()).observe($("curve"));
refresh();
setInterval(refresh, 20000);
