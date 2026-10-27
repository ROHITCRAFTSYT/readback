"use strict";

const $ = (id) => document.getElementById(id);
const canvas = $("scope");
const ctx = canvas.getContext("2d");
const css = getComputedStyle(document.documentElement);
const color = (name) => css.getPropertyValue(name).trim();
const C = {
  scope: color("--scope"), ring: color("--ring"), phosphor: color("--phosphor"),
  dim: color("--phosphor-dim"), err: color("--err"), warn: color("--warn"), muted: color("--muted"),
};

const RANGE_NM = 45;
const TRAIL = 8;
let center = [13.1986, 77.7066];
let aircraft = new Map(); // icao24 -> {callsign, canonical, sv, trail, cleared, alert, alertUntil}
let roster = []; // [{callsign, canonical}]
let source = null;
let liveTimer = null;
let simT = 0;
let stats = { tx: 0, err: 0, track: 0, warn: 0 };

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
    const blink = alerting && ac.alert === "error" && Math.floor(now / 400) % 2 === 0;

    ac.trail.forEach(([lat, lon], i) => {
      const [x, y] = project(lat, lon, w, h);
      ctx.globalAlpha = (i + 1) / (ac.trail.length + 2);
      ctx.fillStyle = col;
      ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
    });
    ctx.globalAlpha = 1;

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
  text.textContent = tx.text;
  li.append(head, text);
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

function onAlert(alert) {
  if (alert.severity === "error") {
    if (alert.source === "track") stats.track += 1;
    else stats.err += 1;
  } else {
    stats.warn += 1;
  }
  const li = document.createElement("li");
  li.className = `alert ${alert.severity}`;
  const kind = alert.kind.replaceAll("_", " ");
  li.innerHTML = `<div class="meta"><span>${fmtTime(alert.t)}</span><span class="sev">${alert.severity}</span><span>${alert.source} · ${kind}</span></div>`;
  const msg = document.createElement("div");
  msg.className = "msg";
  msg.textContent = alert.message;
  li.append(msg);
  prepend("alerts", li);

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

// ---------- modes ----------
function reset() {
  if (source) source.close();
  source = null;
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = null;
  aircraft = new Map();
  stats = { tx: 0, err: 0, track: 0, warn: 0 };
  simT = 0;
  clearList("alerts", "No alerts yet.");
  clearList("radio", "Radio is quiet.");
  setStats();
}

async function play() {
  reset();
  const seed = parseInt($("seed").value, 10) || 0;
  const speed = $("speed").value;
  const info = await fetch(`/api/scenario/${seed}`).then((r) => r.json());
  center = info.center;
  roster = info.aircraft;
  $("scope-note").textContent =
    `Scenario ${info.name} · ${info.aircraft.length} aircraft · ${info.transmissions} transmissions` +
    (info.has_audio ? " · radio audio available" : "");
  $("play").textContent = "Restart";
  source = new EventSource(`/api/replay/${seed}?speed=${speed}`);
  source.addEventListener("tx", (e) => onTx(JSON.parse(e.data)));
  source.addEventListener("states", (e) => onStates(JSON.parse(e.data)));
  source.addEventListener("alert", (e) => onAlert(JSON.parse(e.data)));
  source.addEventListener("end", (e) => {
    const { labels } = JSON.parse(e.data);
    $("scope-note").textContent += ` · finished: ${labels.length} planted problems`;
    source.close();
    $("play").textContent = "Play again";
  });
  source.onerror = () => { if (source) source.close(); };
}

async function pollLive() {
  try {
    const res = await fetch("/api/live");
    if (!res.ok) throw new Error((await res.json()).detail);
    const data = await res.json();
    center = data.center;
    const seen = new Set();
    for (const sv of data.states) {
      seen.add(sv.icao24);
      upsert(sv);
    }
    for (const key of [...aircraft.keys()]) if (!seen.has(key)) aircraft.delete(key);
    $("scope-note").textContent =
      `Live ADS-B around Bengaluru from OpenSky · ${data.states.length} airborne · ` +
      `no radio feed in live mode · updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    $("scope-note").textContent = `Live traffic unavailable: ${err.message}`;
  }
}

function setMode(mode) {
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.mode === mode;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", String(on));
  });
  document.body.classList.toggle("live", mode === "live");
  reset();
  if (mode === "live") {
    pollLive();
    liveTimer = setInterval(pollLive, 15000);
  } else {
    $("scope-note").textContent = "Press Play to replay a simulated Bengaluru approach frequency.";
    $("play").textContent = "Play";
  }
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));
$("play").addEventListener("click", play);
setInterval(() => { $("clock").textContent = document.body.classList.contains("live") ? new Date().toLocaleTimeString().slice(0, 5) : fmtTime(simT); }, 250);

resize();
requestAnimationFrame(frame);
