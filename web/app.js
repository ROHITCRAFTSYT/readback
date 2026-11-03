"use strict";

const $ = (id) => document.getElementById(id);
const canvas = $("scope");
const ctx = canvas.getContext("2d");
const css = getComputedStyle(document.documentElement);
const color = (name) => css.getPropertyValue(name).trim();
const C = {
  scope: color("--scope"), ring: color("--ring"), phosphor: color("--phosphor"),
  dim: color("--phosphor-dim"), err: color("--err"), warn: color("--warn"), muted: color("--muted"),
  accent: color("--accent"),
};
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

const RANGE_NM = 45;
const TRAIL = 8;
let center = [13.1986, 77.7066];
let aircraft = new Map(); // icao24 -> {callsign, canonical, sv, trail, cleared, alert, alertUntil, focusUntil}
let roster = []; // [{callsign, canonical}]
let source = null;
let liveTimer = null;
let simT = 0;
let stats = { tx: 0, err: 0, track: 0, warn: 0 };
let parserName = "tagger";
let sessionId = null; // the server's record of this replay or recording, for reports
let museOK = false;
let runId = 0; // bumped by reset(), so a replay or recording check that was overtaken stops
let txs = []; // [{t, callsign, id, el}] in arrival order, for locating an alert's transmission
let alertEls = new Map(); // alert index -> {alert, li}
let unacked = []; // streamed error alerts not yet acknowledged, oldest first
const PARSERS = {
  tagger: "Tagger (on-prem)", spark: "Muse Spark", glimmer: "Muse Glimmer (fine-tuned)", rules: "Rules",
};

// ---------- callsign matching (mirrors readback.compare.same_callsign) ----------
function tail(cs) {
  const t = cs.split(" ");
  return t.length && t[0].length > 1 ? t.slice(1) : t;
}
function sameCallsign(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  const ta = tail(a), tb = tail(b);
  const [s, l] = ta.length <= tb.length ? [ta, tb] : [tb, ta];
  return s.length >= 2 && l.slice(l.length - s.length).join(" ") === s.join(" ");
}
function aircraftFor(canonical) {
  for (const ac of aircraft.values()) if (sameCallsign(ac.canonical, canonical)) return ac;
  return null;
}

// ---------- scope ----------
function resize() {
  const r = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
new ResizeObserver(resize).observe(canvas.parentElement);

function project(lat, lon, w, h) {
  const scale = (Math.min(w, h) / 2 - 24) / RANGE_NM;
  const x = (lon - center[1]) * Math.cos((center[0] * Math.PI) / 180) * 60;
  const y = (lat - center[0]) * 60;
  return [w / 2 + x * scale, h / 2 - y * scale, scale];
}

function drawBackground(w, h) {
  ctx.fillStyle = C.scope;
  ctx.fillRect(0, 0, w, h);
  const [cx, cy, scale] = project(center[0], center[1], w, h);
  ctx.strokeStyle = C.ring;
  ctx.lineWidth = 1;
  ctx.font = "11px 'IBM Plex Mono', monospace";
  ctx.fillStyle = C.dim;
  for (let nm = 10; nm <= 40; nm += 10) {
    ctx.beginPath();
    ctx.arc(cx, cy, nm * scale, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillText(`${nm}`, cx + 3, cy - nm * scale - 3);
  }
  for (let deg = 0; deg < 360; deg += 10) {
    const a = ((deg - 90) * Math.PI) / 180;
    const r = 40 * scale;
    const len = deg % 30 === 0 ? 10 : 5;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
    ctx.lineTo(cx + Math.cos(a) * (r + len), cy + Math.sin(a) * (r + len));
    ctx.stroke();
    if (deg % 30 === 0) {
      const label = String(deg).padStart(3, "0");
      ctx.fillText(label, cx + Math.cos(a) * (r + 20) - 11, cy + Math.sin(a) * (r + 20) + 4);
    }
  }
  // Runways 09L/27R and 09R/27L, roughly 2 NM long.
  ctx.strokeStyle = C.dim;
  ctx.lineWidth = 3;
  for (const off of [-0.25, 0.25]) {
    ctx.beginPath();
    ctx.moveTo(cx - 1.1 * scale, cy + off * scale);
    ctx.lineTo(cx + 1.1 * scale, cy + off * scale);
    ctx.stroke();
  }
  ctx.lineWidth = 1;
  ctx.fillText("VOBL", cx + 1.4 * scale, cy + 4);
}

// Data block offsets tried in order; each aircraft takes the first that overlaps least.
const OFFSETS = [[14, -26], [14, 14], [-86, -26], [-86, 14]];
const BLOCK = [72, 30];

function overlap(a, b) {
  const dx = Math.max(0, Math.min(a[0] + BLOCK[0], b[0] + BLOCK[0]) - Math.max(a[0], b[0]));
  const dy = Math.max(0, Math.min(a[1] + BLOCK[1], b[1] + BLOCK[1]) - Math.max(a[1], b[1]));
  return dx * dy;
}

function drawAircraft(w, h, now) {
  ctx.font = "11px 'IBM Plex Mono', monospace";
  const placed = [];
  const blips = [...aircraft.values()]
    .filter((ac) => ac.sv && ac.sv.lat != null)
    .map((ac) => [ac, ...project(ac.sv.lat, ac.sv.lon, w, h)]);
  for (const [, x, y] of blips) placed.push([x - 5, y - 5 - BLOCK[1] / 2]); // keep blocks off blips
  for (const [ac, x, y] of blips) {
    const sv = ac.sv;
    const alerting = ac.alertUntil > now;
    const col = alerting ? (ac.alert === "error" ? C.err : C.warn) : C.phosphor;
    const blink = alerting && ac.alert === "error" && (reducedMotion.matches || Math.floor(now / 400) % 2 === 0);

    ac.trail.forEach(([lat, lon], i) => {
      const [x, y] = project(lat, lon, w, h);
      ctx.globalAlpha = (i + 1) / (ac.trail.length + 2);
      ctx.fillStyle = col;
      ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
    });
    ctx.globalAlpha = 1;

    if (ac.focusUntil > now) {
      // Located from an alert card: a ring around the blip, pulsing unless motion is reduced.
      const r = reducedMotion.matches ? 16 : 13 + 5 * Math.abs(Math.sin(now / 250));
      ctx.strokeStyle = C.accent;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.strokeStyle = col;
    ctx.lineWidth = blink ? 2.5 : 1.5;
    ctx.strokeRect(x - 4, y - 4, 8, 8);
    if (sv.track_deg != null && sv.ground_speed_kt) {
      const a = ((sv.track_deg - 90) * Math.PI) / 180;
      const len = 14 + sv.ground_speed_kt / 25;
      ctx.beginPath();
      ctx.moveTo(x + Math.cos(a) * 5, y + Math.sin(a) * 5);
      ctx.lineTo(x + Math.cos(a) * len, y + Math.sin(a) * len);
      ctx.stroke();
    }
    // Data block: callsign / level + trend + cleared level / ground speed
    let best = OFFSETS[0], bestCost = Infinity;
    for (const [ox, oy] of OFFSETS) {
      const box = [x + ox, y + oy - 10];
      const cost = placed.reduce((s, p) => s + overlap(box, p), 0);
      if (cost < bestCost) [best, bestCost] = [[ox, oy], cost];
      if (cost === 0) break;
    }
    const bx = x + best[0], by = y + best[1];
    placed.push([bx, by - 10]);
    ctx.strokeStyle = C.dim;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x + Math.sign(best[0]) * 5, y + Math.sign(best[1]) * 5);
    ctx.lineTo(best[0] > 0 ? bx - 2 : bx + BLOCK[0] - 4, by + (best[1] < 0 ? 8 : -8));
    ctx.stroke();
    const fl = sv.altitude_ft != null ? String(Math.round(sv.altitude_ft / 100)).padStart(3, "0") : "---";
    const vr = sv.vertical_rate_fpm || 0;
    const trend = vr > 300 ? "↑" : vr < -300 ? "↓" : " ";
    const cfl = ac.cleared.level ? ` ${ac.cleared.level}` : "";
    ctx.fillStyle = col;
    ctx.fillText(sv.callsign, bx, by);
    ctx.fillText(`${fl}${trend}${cfl}`, bx, by + 13);
    ctx.fillStyle = alerting ? col : C.muted;
    const hdg = ac.cleared.heading ? ` H${ac.cleared.heading}` : "";
    ctx.fillText(`${Math.round(sv.ground_speed_kt || 0)}${hdg}`, bx, by + 26);
  }
}

function frame() {
  requestAnimationFrame(frame); // schedule first so a drawing error can't stop the loop
  const r = canvas.getBoundingClientRect();
  if (Math.min(r.width, r.height) < 120) return; // too small to draw (layout in progress)
  drawBackground(r.width, r.height);
  drawAircraft(r.width, r.height, performance.now());
}

// ---------- panels ----------
function fmtTime(t) {
  const s = Math.max(0, Math.round(t));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
// Mirrors readback.monitor.describe for the values readback alerts carry.
function describe(kind, value) {
  const v = String(value);
  if (kind === "flight_level") return /^\d+$/.test(v) ? `FL${v.padStart(3, "0")}` : `FL${v}`;
  if (kind === "callsign") {
    const t = v.split(" ").filter(Boolean);
    return t.length && t[0].length > 1 ? `${t[0]} ${t.slice(1).join("")}`.toUpperCase() : t.join("").toUpperCase();
  }
  const fmt = {
    altitude: `${v} ft`, heading: `heading ${v}`, speed: `${v} kt`, squawk: `squawk ${v}`,
    frequency: `${v} MHz`, qnh: `QNH ${v}`, runway: `runway ${v.toUpperCase()}`,
    cleared_to_land: `cleared to land ${v.toUpperCase()}`.trim(),
    cleared_for_takeoff: `cleared for takeoff ${v.toUpperCase()}`.trim(),
    line_up: `line up ${v.toUpperCase()}`.trim(), hold_short: `hold short ${v.toUpperCase()}`.trim(),
    direct_to: `direct ${v.toUpperCase()}`,
  };
  return fmt[kind] || `${kind} ${v}`;
}
function clearList(id, empty) {
  $(id).innerHTML = `<li class="empty">${empty}</li>`;
}
function prepend(id, el) {
  const list = $(id);
  const empty = list.querySelector(".empty");
  if (empty) empty.remove();
  list.prepend(el);
}
function setStats() {
  $("stat-tx").textContent = stats.tx;
  $("stat-err").textContent = stats.err;
  $("stat-track").textContent = stats.track;
  $("stat-warn").textContent = stats.warn;
  $("alert-count").textContent = stats.err + stats.track + stats.warn;
}

function onTx(tx) {
  stats.tx += 1;
  simT = tx.t;
  const li = document.createElement("li");
  li.className = "tx";
  li.id = `tx-${tx.id}`;
  const head = document.createElement("div");
  head.className = "head";
  head.innerHTML = `<span>${fmtTime(tx.t)}</span><span class="role ${tx.role}">${tx.role === "controller" ? "ATC" : "PILOT"}</span>`;
  if (tx.callsign) {
    const cs = document.createElement("span");
    cs.className = "cs";
    cs.textContent = tx.callsign_label || tx.callsign;
    head.append(cs);
  }
  const text = document.createElement("div");
  text.className = "text";
  text.textContent = tx.asr_error ? "(not transcribed)" : tx.text;
  li.append(head, text);
  if (tx.parse_error) {
    const via = document.createElement("div");
    via.className = "via";
    const s = document.createElement("span");
    s.className = "fail";
    s.textContent = `${PARSERS[parserName]} failed: ${tx.parse_error}`;
    via.append(s);
    li.append(via);
  }
  if (tx.script != null) {
    // Listening: show the script when the transcript differs, and what each step took.
    const words = (s) => s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter(Boolean).join(" ");
    if (words(tx.script) !== words(tx.text)) {
      const said = document.createElement("div");
      said.className = "said";
      said.textContent = tx.script;
      li.append(said);
    }
    const via = document.createElement("div");
    via.className = "via";
    const part = (cls, label) => {
      const s = document.createElement("span");
      if (cls) s.className = cls;
      s.textContent = label;
      via.append(s);
    };
    if (tx.asr_error) part("fail", `Voice Transcribe failed: ${tx.asr_error}`);
    else part("", `Voice Transcribe ${tx.asr_s != null ? `${tx.asr_s.toFixed(1)} s` : "(cached)"}`);
    if (tx.parse_s != null) part("", `${PARSERS[parserName]} ${tx.parse_s < 0.05 ? "<0.1" : tx.parse_s.toFixed(1)} s`);
    if (tx.snapped_from) part("snap", `heard ${tx.snapped_from.toUpperCase()}, matched to ADS-B`);
    li.append(via);
  }
  if (tx.items.length) {
    const chips = document.createElement("div");
    chips.className = "chips";
    for (const item of tx.items) {
      const c = document.createElement("span");
      c.className = "chip";
      const q = item.qualifier ? ` ${item.qualifier}` : "";
      c.textContent = item.label + q;
      chips.append(c);
    }
    li.append(chips);
  }
  prepend("radio", li);
  txs.push({ t: tx.t, callsign: tx.callsign, id: tx.id, el: li });

  if (tx.role === "controller" && tx.callsign) {
    const ac = aircraftFor(tx.callsign);
    if (ac) {
      for (const item of tx.items) {
        if (item.kind === "flight_level") ac.cleared.level = item.label.replace("FL", "");
        if (item.kind === "altitude") ac.cleared.level = `A${Math.round(parseInt(item.label, 10) / 100)}`;
        if (item.kind === "heading") ac.cleared.heading = item.label.replace("heading ", "");
      }
    }
  }
  if (tx.audio && $("sound").checked) new Audio(tx.audio).play().catch(() => {});
  setStats();
}

function onStates(msg) {
  simT = msg.t;
  for (const sv of msg.states) upsert(sv);
}

function upsert(sv) {
  let ac = aircraft.get(sv.icao24);
  if (!ac) {
    const known = roster.find((a) => a.callsign === sv.callsign);
    ac = {
      callsign: sv.callsign, canonical: known ? known.canonical : sv.callsign.toLowerCase(),
      sv: null, trail: [], cleared: {}, alert: null, alertUntil: 0,
    };
    aircraft.set(sv.icao24, ac);
  }
  if (ac.sv && ac.sv.lat != null) {
    ac.trail.push([ac.sv.lat, ac.sv.lon]);
    if (ac.trail.length > TRAIL) ac.trail.shift();
  }
  ac.sv = sv;
}

// Expected and heard under the message: what was cleared, and what was read back or flown.
function expectedHeard(alert) {
  if (!alert.expected && !alert.heard) return null;
  const track = alert.source === "track";
  const show = (v) => (!v ? "nothing" : track ? v : describe(alert.kind, v));
  const labels = track ? ["Cleared", "Flying"] : alert.kind === "callsign" ? ["Sent to", "Read back by"] : ["Cleared", "Read back"];
  const row = document.createElement("div");
  row.className = "eh";
  for (const [label, value, cls] of [
    [labels[0], alert.expected, "v"],
    [labels[1], alert.heard, "v heard"],
  ]) {
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = label;
    const v = document.createElement("span");
    v.className = cls;
    v.textContent = show(value);
    row.append(k, v);
  }
  return row;
}

// streamed: the alert arrived live from a replay, so it can raise the banner and the chime.
function onAlert(alert, streamed = false) {
  if (alert.severity === "error") {
    if (alert.source === "track") stats.track += 1;
    else stats.err += 1;
  } else {
    stats.warn += 1;
  }
  const li = document.createElement("li");
  li.className = `alert ${alert.severity} locatable`;
  li.tabIndex = 0;
  li.title = "Show its transmission and aircraft";
  if (alert.index != null) li.dataset.index = alert.index;
  const kind = alert.kind.replaceAll("_", " ");
  li.innerHTML = `<div class="meta"><span>${fmtTime(alert.t)}</span><span class="sev">${alert.severity}</span><span>${alert.source} · ${kind}</span></div>`;
  const msg = document.createElement("div");
  msg.className = "msg";
  msg.textContent = alert.message;
  li.append(msg);
  const eh = expectedHeard(alert);
  if (eh) li.append(eh);
  if (museOK && sessionId && alert.index != null) {
    const btn = document.createElement("button");
    btn.className = "draft";
    btn.textContent = "Draft occurrence report";
    const sid = sessionId;
    btn.addEventListener("click", (e) => { e.stopPropagation(); draftReport(sid, alert.index, btn); });
    li.append(btn);
  }
  const entry = { alert, li };
  li.addEventListener("click", () => locate(entry));
  li.addEventListener("keydown", (e) => {
    if (e.target === li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); locate(entry); }
  });
  prepend("alerts", li);
  if (alert.index != null) alertEls.set(alert.index, entry);
  if (streamed && alert.severity === "error") {
    li.classList.add("unack");
    unacked.push(entry);
    renderPriority();
    chime();
  }

  const ac = aircraftFor(alert.callsign);
  if (ac) {
    ac.alert = ac.alertUntil > performance.now() && ac.alert === "error" ? "error" : alert.severity;
    ac.alertUntil = performance.now() + 20000;
  }
  if (alert.ref && alert.severity === "error") {
    const line = document.getElementById(`tx-${alert.ref}`);
    if (line) line.classList.add("flagged");
  }
  setStats();
}

// The transmission behind an alert: its ref when the line is on the list, otherwise the latest
// transmission from the same callsign at or before the alert time.
function txFor(alert) {
  const byRef = alert.ref && txs.find((x) => x.id === alert.ref);
  if (byRef) return byRef;
  let best = null;
  for (const x of txs) if (x.t <= alert.t + 0.01 && sameCallsign(x.callsign, alert.callsign)) best = x;
  return best;
}

// Scroll a list item into view inside its own list, without moving the page.
function showInList(el) {
  const list = el.parentElement;
  const l = list.getBoundingClientRect(), r = el.getBoundingClientRect();
  const behavior = reducedMotion.matches ? "auto" : "smooth";
  if (r.top < l.top) list.scrollBy({ top: r.top - l.top - 6, behavior });
  else if (r.bottom > l.bottom) list.scrollBy({ top: r.bottom - l.bottom + 6, behavior });
}

let locatedTimer = null;
function locate(entry) {
  document.querySelectorAll(".alert.selected, .tx.located").forEach((el) => el.classList.remove("selected", "located"));
  entry.li.classList.add("selected");
  showInList(entry.li);
  const tx = txFor(entry.alert);
  if (tx) {
    void tx.el.offsetWidth; // restart the highlight animation when the same line is located twice
    tx.el.classList.add("located");
    showInList(tx.el);
    clearTimeout(locatedTimer);
    locatedTimer = setTimeout(() => tx.el.classList.remove("located"), 4000);
  }
  const ac = aircraftFor(entry.alert.callsign);
  if (ac) ac.focusUntil = performance.now() + 4000;
}

// ---------- unacknowledged errors: banner, count, chime ----------
function renderPriority() {
  const n = unacked.length;
  $("priority").hidden = n === 0;
  $("unack-count").hidden = n === 0;
  $("unack-count").textContent = `${n} new`;
  $("unack-count").title = `${n} error${n === 1 ? "" : "s"} not acknowledged`;
  document.title = `${n ? `(${n}) ` : ""}Readback · Monitor`;
  if (!n) return;
  const { alert } = unacked[n - 1];
  $("priority-kind").textContent = alert.source === "track" ? "Track deviation" : "Readback error";
  $("priority-cs").textContent = alert.callsign ? describe("callsign", alert.callsign) : "";
  $("priority-msg").textContent = alert.message;
  $("priority-ack").textContent =
    `Click or press A or Enter to acknowledge${n > 1 ? ` · ${n - 1} more unacknowledged` : ""}`;
}

function acknowledge() {
  const entry = unacked.pop();
  if (!entry) return;
  entry.li.classList.remove("unack");
  renderPriority();
}

$("priority").addEventListener("click", () => {
  const entry = unacked[unacked.length - 1];
  if (entry) locate(entry);
  acknowledge();
});
document.addEventListener("keydown", (e) => {
  if (!unacked.length || e.ctrlKey || e.metaKey || e.altKey || $("report").open) return;
  if (e.target.closest && e.target.closest("input, select, textarea, [contenteditable]")) return;
  // Enter acknowledges when nothing else has focus, or when focus is still on Play / Run demo
  // after starting the replay (pressing it there would otherwise restart the replay).
  const enter = e.key === "Enter" &&
    (e.target === document.body || e.target === document.documentElement ||
     e.target === $("play") || e.target === $("demo"));
  if (e.key === "a" || e.key === "A" || enter) {
    e.preventDefault();
    acknowledge();
  }
});

// Optional two-tone chime for error alerts, off unless the user turns it on.
const CHIME_KEY = "readback.chime";
let audioCtx = null;
let lastChime = 0;
try { $("chime").checked = localStorage.getItem(CHIME_KEY) === "on"; } catch { /* storage blocked */ }
$("chime").addEventListener("change", () => {
  try { localStorage.setItem(CHIME_KEY, $("chime").checked ? "on" : "off"); } catch { /* storage blocked */ }
  if ($("chime").checked) chime(true);
});
function chime(force = false) {
  if (!$("chime").checked) return;
  const now = performance.now();
  if (!force && now - lastChime < 1500) return; // one chime for a burst of alerts
  lastChime = now;
  try {
    audioCtx = audioCtx || new AudioContext();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const t0 = audioCtx.currentTime;
    for (const [i, hz] of [[0, 880], [1, 660]]) {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.frequency.value = hz;
      const start = t0 + i * 0.18;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.2, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(start);
      osc.stop(start + 0.17);
    }
  } catch { /* no WebAudio here */ }
}

// ---------- scorecard ----------
function renderScore(score) {
  const card = $("scorecard");
  card.innerHTML = "";
  const head = document.createElement("div");
  head.className = "sc-head";
  const title = document.createElement("b");
  title.textContent = `Caught ${score.caught} of ${score.planted} planted problems`;
  const close = document.createElement("button");
  close.className = "sc-close";
  close.type = "button";
  close.setAttribute("aria-label", "Close result");
  close.textContent = "×";
  close.addEventListener("click", () => { card.hidden = true; });
  head.append(title, close);

  const sum = document.createElement("div");
  sum.className = "sc-sum";
  const missed = score.planted - score.caught;
  const stat = (n, label, cls) => {
    const s = document.createElement("span");
    s.innerHTML = `<span class="n ${cls}">${n}</span> ${label}`;
    sum.append(s);
  };
  stat(score.caught, "caught", "ok");
  stat(missed, "missed", missed ? "err" : "");
  stat(score.false_errors, `false error alert${score.false_errors === 1 ? "" : "s"}`, score.false_errors ? "err" : "");
  if (score.unmatched_warnings) stat(score.unmatched_warnings, "unplanted warnings", "");

  const list = document.createElement("ol");
  for (const lb of [...score.labels].sort((a, b) => Number(a.caught) - Number(b.caught) || a.t - b.t)) {
    const li = document.createElement("li");
    const entry = lb.alert_index != null ? alertEls.get(lb.alert_index) : null;
    const row = document.createElement(entry ? "button" : "div");
    if (entry) {
      row.type = "button";
      row.title = "Show the alert that caught it";
      row.addEventListener("click", () => locate(entry));
    }
    const st = document.createElement("span");
    st.className = `st ${lb.caught ? "caught" : "missed"}`;
    st.textContent = lb.caught ? "Caught" : "Missed";
    const at = document.createElement("span");
    at.textContent = fmtTime(lb.t);
    const what = document.createElement("span");
    what.className = "what";
    const d = lb.caught && lb.delay_s != null ? Math.round(lb.delay_s) : null;
    const delay = d == null ? "" : d < 0 ? ` · ${-d} s early` : ` · after ${d} s`;
    const cs = lb.callsign ? `${lb.callsign} · ` : "";
    what.textContent = `${cs}${lb.detail || lb.kind.replaceAll("_", " ")}${delay}`;
    row.append(st, at, what);
    li.append(row);
    list.append(li);
  }
  card.append(head, sum, list);
  card.hidden = false;
}

// ---------- occurrence reports ----------
async function draftReport(sid, index, btn) {
  btn.disabled = true;
  btn.textContent = "Investigating…";
  try {
    const res = await api(`/api/report/${sid}/${index}`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    $("report-body").innerHTML = renderMarkdown(data.markdown);
    const dl = $("report-dl");
    if (dl.href.startsWith("blob:")) URL.revokeObjectURL(dl.href);
    dl.href = URL.createObjectURL(new Blob([data.markdown], { type: "text/markdown" }));
    $("report").showModal();
    btn.textContent = "Open occurrence report";
    btn.disabled = false;
  } catch (err) {
    btn.textContent = `Report failed: ${err.message}. Retry`;
    btn.disabled = false;
  }
}
$("report-close").addEventListener("click", () => $("report").close());

// ---------- recordings ----------
async function analyzeRecording() {
  const file = $("rec-file").files[0];
  if (!file) { $("scope-note").textContent = "Choose an audio file first."; return; }
  reset();
  const mine = runId; // a mode switch or a new check makes this one stop writing
  try {
    museOK = (await fetch("/healthz").then((r) => r.json())).muse;
  } catch {
    if (mine === runId) $("scope-note").textContent = "Cannot reach the Readback server.";
    return;
  }
  if (mine !== runId) return;
  if (!museOK) { $("scope-note").textContent = "Checking recordings needs MODEL_API_KEY on the server."; return; }
  parserName = $("parser").value;
  $("analyze").disabled = true;
  $("scope-note").textContent = `Uploading ${file.name}…`;
  try {
    const q = new URLSearchParams({ name: file.name, callsigns: $("rec-cs").value, parser: parserName });
    const res = await api(`/api/recordings?${q}`, { method: "POST", body: file });
    const queued = await res.json();
    if (mine !== runId) return;
    if (!res.ok) throw new Error(queued.detail || res.statusText);
    let data;
    for (;;) {  // checked in the background; long recordings take a while
      data = await api(`/api/recordings/${queued.id}`).then((r) => r.json());
      if (mine !== runId) return;
      if (data.status === "done") break;
      if (data.status === "failed") throw new Error(data.error);
      $("scope-note").textContent =
        `Transcribing ${file.name} with Voice Transcribe: ${Math.round(data.progress * 100)}% …`;
      await new Promise((r) => setTimeout(r, 1500));
      if (mine !== runId) return;
    }
    sessionId = data.id;
    for (const tx of data.transmissions) onTx(tx);
    for (const alert of [...data.alerts].sort((a, b) => a.t - b.t)) onAlert(alert);
    simT = data.duration;
    const audio = $("rec-audio");
    audio.src = data.audio;
    audio.hidden = false;
    $("scope-note").innerHTML =
      `${escapeHtml(file.name)} · ${fmtTime(data.duration)} · ${data.transmissions.length} transmissions · ` +
      `${data.alerts.length} alerts · Voice Transcribe → ${PARSERS[parserName]} · no ADS-B for recordings · ` +
      `<a href="/review#${data.id}">open in Review to give verdicts</a>`;
  } catch (err) {
    if (mine === runId) $("scope-note").textContent = `Could not check the recording: ${err.message}`;
  } finally {
    $("analyze").disabled = false;
  }
}
$("analyze").addEventListener("click", analyzeRecording);

// ---------- modes ----------
function reset() {
  runId += 1;
  if (source) source.close();
  source = null;
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = null;
  aircraft = new Map();
  sessionId = null;
  $("rec-audio").hidden = true;
  stats = { tx: 0, err: 0, track: 0, warn: 0 };
  simT = 0;
  txs = [];
  alertEls = new Map();
  unacked = [];
  renderPriority();
  $("scorecard").hidden = true;
  clearList("alerts", "No alerts yet.");
  clearList("radio", "Radio is quiet.");
  setStats();
}

function stopped(note) {
  $("scope-note").textContent = note;
  $("play").textContent = "Restart";
}

async function play() {
  reset();
  const mine = runId; // a second click or a mode switch overtakes this replay
  $("onboard").hidden = true;
  const seed = parseInt($("seed").value, 10) || 0;
  const speed = $("speed").value;
  let info;
  try {
    const res = await api(`/api/scenario/${seed}`);
    const data = await res.json().catch(() => ({}));
    if (mine !== runId) return;
    if (!res.ok) { stopped(`Scenario unavailable: ${data.detail || res.status}`); return; }
    info = data;
  } catch {
    if (mine === runId) stopped("Lost connection to the server. Press Restart to try again.");
    return;
  }
  center = info.center;
  roster = info.aircraft;
  let listen = $("hear").value === "muse";
  parserName = $("parser").value;
  if (parserName === "tagger" && info.parser === "rules") parserName = "rules"; // no model here
  if (!info.muse && (listen || parserName === "spark")) {
    // No API key on this server: fall back to what runs locally.
    listen = false;
    if (parserName === "spark") parserName = info.parser;
    $("hear").value = "script";
    $("parser").value = parserName;
  }
  if (parserName === "glimmer" && !info.glimmer) {
    parserName = info.parser; // no fine-tuned Glimmer deployed for this server
    $("parser").value = parserName;
  }
  if (listen) $("sound").checked = true;
  const how = listen ? `Voice Transcribe → ${PARSERS[parserName]}` : `script → ${PARSERS[parserName]}`;
  $("scope-note").textContent =
    `Scenario ${info.name} · ${info.aircraft.length} aircraft · ${info.transmissions} transmissions · ${how}` +
    (!info.muse ? " · Muse unavailable (no API key)" : "");
  $("play").textContent = "Restart";
  const es = new EventSource(`/api/replay/${seed}?speed=${speed}&listen=${listen}&parser=${parserName}`);
  source = es;
  museOK = info.muse;
  es.addEventListener("session", (e) => { sessionId = JSON.parse(e.data).id; });
  es.addEventListener("tx", (e) => onTx(JSON.parse(e.data)));
  es.addEventListener("states", (e) => onStates(JSON.parse(e.data)));
  es.addEventListener("alert", (e) => onAlert(JSON.parse(e.data), true));
  es.addEventListener("end", (e) => {
    es.close();
    const { labels, score } = JSON.parse(e.data);
    $("scope-note").textContent += ` · finished: ${labels.length} planted problems`;
    if (score) renderScore(score);
    $("play").textContent = "Play again";
  });
  // Both the server's `event: error` (with a detail) and a dropped connection land here.
  es.addEventListener("error", (e) => {
    es.close();
    if (source !== es) return;
    source = null;
    let detail = null;
    try { detail = e.data ? JSON.parse(e.data).detail : null; } catch { detail = e.data; }
    // The server's detail already reads "replay stopped: ...", so only capitalise it.
    const msg = detail ? String(detail) : "";
    stopped(msg ? msg[0].toUpperCase() + msg.slice(1) : "Lost connection to the server. Press Restart to try again.");
  });
}

function runDemo() {
  $("seed").value = 7;
  $("speed").value = "10";
  $("hear").value = "script";
  $("parser").value = $("parser").querySelector('option[value="tagger"]').disabled ? "rules" : "tagger";
  play();
}
$("demo").addEventListener("click", runDemo);

// Grey out the speech and parser options this server cannot run, with the reason in the label.
const OPTION_NOTE = { muse: "needs API key", spark: "needs API key", glimmer: "not deployed", tagger: "model not installed" };
function markOptions(h) {
  const off = { muse: !h.muse, spark: !h.muse, glimmer: !h.glimmer, tagger: h.parser === "rules" };
  for (const sel of [$("hear"), $("parser")]) {
    for (const opt of sel.options) {
      if (!(opt.value in off)) continue;
      opt.dataset.label = opt.dataset.label || opt.textContent;
      opt.disabled = off[opt.value];
      opt.textContent = off[opt.value] ? `${opt.dataset.label}: ${OPTION_NOTE[opt.value]}` : opt.dataset.label;
    }
    if (sel.selectedOptions[0] && sel.selectedOptions[0].disabled) {
      sel.value = sel === $("hear") ? "script" : h.parser;
    }
  }
  const hint = $("opt-hint");
  hint.hidden = h.muse && h.glimmer;
  hint.textContent = !h.muse
    ? "Muse options need MODEL_API_KEY on the server."
    : "The fine-tuned Glimmer is not deployed for this server.";
  if (h.parser === "rules") {
    $("demo-note").textContent = "Scenario 7 at 10× with scripted speech and the Rules parser. No API key needed.";
  }
}
fetch("/healthz").then((r) => r.json()).then((h) => {
  museOK = h.muse;
  markOptions(h);
}).catch(() => {});

async function pollLive() {
  const mine = runId;
  try {
    const res = await api("/api/live");
    if (!res.ok) throw new Error((await res.json()).detail);
    const data = await res.json();
    if (mine !== runId) return; // left live mode while this poll was in flight
    center = data.center;
    const seen = new Set();
    for (const sv of data.states) {
      seen.add(sv.icao24);
      upsert(sv);
    }
    for (const key of [...aircraft.keys()]) if (!seen.has(key)) aircraft.delete(key);
    $("scope-note").textContent =
      `Live ADS-B around ${data.name} from OpenSky · ${data.states.length} airborne · ` +
      `no radio feed in live mode · updated ${new Date().toISOString().slice(11, 19)} UTC`;
  } catch (err) {
    if (mine === runId) $("scope-note").textContent = `Live traffic unavailable: ${err.message}`;
  }
}

function setMode(mode) {
  document.querySelectorAll(".tab[data-mode]").forEach((t) => {
    const on = t.dataset.mode === mode;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", String(on));
  });
  document.body.classList.toggle("live", mode === "live");
  document.body.classList.toggle("recording", mode === "recording");
  reset();
  $("onboard").hidden = mode !== "replay";
  $("clock-label").textContent = { live: "UTC", recording: "Recording time", replay: "Scenario time" }[mode];
  if (mode === "live") {
    pollLive();
    liveTimer = setInterval(pollLive, 15000);
  } else if (mode === "recording") {
    $("scope-note").textContent =
      "Check a recorded frequency: choose an audio file (up to 4 hours) and press Check recording. Past recordings and verdicts are under Review.";
  } else {
    $("scope-note").textContent = "Press Play to replay a simulated Bengaluru approach frequency.";
    $("play").textContent = "Play";
  }
}

document.querySelectorAll(".tab[data-mode]").forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));
if (location.hash === "#live") setMode("live");
if (location.hash === "#demo") runDemo();
$("play").addEventListener("click", play);
setInterval(() => { $("clock").textContent = document.body.classList.contains("live") ? new Date().toISOString().slice(11, 16) : fmtTime(simT); }, 250);

resize();
requestAnimationFrame(frame);
