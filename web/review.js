// Recording review: upload recordings, follow the background checks, and give every alert a
// verdict. Verdicts are stored on the server and make up the unit's alert register (CSV).
const $ = (id) => document.getElementById(id);
const PARSERS = {
  tagger: "Tagger (on-prem)", spark: "Muse Spark", glimmer: "Muse Glimmer (fine-tuned)", rules: "Rules",
};
const VERDICT_LABEL = { open: "to review", confirmed: "confirmed", false_alarm: "false alarm" };
let items = [];
let selected = null;
let detail = null;
let listTimer = null;
let detailTimer = null;
const narrow = matchMedia("(max-width: 1000px)"); // where review.css stacks the list over the detail
const motion = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");

function fmtTime(t) {
  const s = Math.max(0, Math.floor(t));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return hh ? `${hh}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
}
const fmtDate = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
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
    frequency: `${v} MHz`, qnh: `QNH ${v}`, runway: `runway ${v.toUpperCase()}`, direct_to: `direct ${v.toUpperCase()}`,
    cleared_to_land: `cleared to land ${v.toUpperCase()}`.trim(),
    cleared_for_takeoff: `cleared for takeoff ${v.toUpperCase()}`.trim(),
    line_up: `line up ${v.toUpperCase()}`.trim(), hold_short: `hold short ${v.toUpperCase()}`.trim(),
  };
  return fmt[kind] || `${kind} ${v}`;
}

// ---------- reviewer ----------
try { $("reviewer").value = localStorage.getItem("readback-reviewer") || ""; } catch {}
$("reviewer").addEventListener("change", () => {
  try { localStorage.setItem("readback-reviewer", $("reviewer").value.trim()); } catch {}
});

// ---------- position filter: one unit, or one unit's frequency ----------
let position = "";
try { position = localStorage.getItem("readback-position") || ""; } catch {}
const posLabel = (p) => [p.unit || "No unit", p.frequency].filter(Boolean).join(" · ");
const posQuery = () => {
  if (!position) return "";
  const p = JSON.parse(position);
  const q = new URLSearchParams({ unit: p.unit });
  if (p.frequency != null) q.set("frequency", p.frequency);
  return q.toString();
};

function renderPositions(positions) {
  const sel = $("pos");
  const opts = [["", "All positions"]];
  const units = [...new Set(positions.map((p) => p.unit))];
  for (const unit of units) {
    const own = positions.filter((p) => p.unit === unit);
    const n = own.reduce((t, p) => t + p.recordings, 0);
    if (own.length > 1) opts.push([JSON.stringify({ unit }), `${unit || "No unit"}, all frequencies (${n})`]);
    for (const p of own) {
      opts.push([JSON.stringify({ unit, frequency: p.frequency }), `${posLabel(p)} (${p.recordings})`]);
    }
  }
  if (position && !opts.some(([v]) => v === position)) position = "";
  sel.replaceChildren(...opts.map(([v, label]) => {
    const o = el("option", "", label);
    o.value = v;
    return o;
  }));
  sel.value = position;
  sel.hidden = false;
  $("units").replaceChildren(...units.filter(Boolean).map((u) => Object.assign(el("option"), { value: u })));
  const freqs = [...new Set(positions.map((p) => p.frequency).filter(Boolean))];
  $("freqs").replaceChildren(...freqs.map((f) => Object.assign(el("option"), { value: f })));
}
$("pos").addEventListener("change", () => {
  position = $("pos").value;
  try { localStorage.setItem("readback-position", position); } catch {}
  loadList();
});

// ---------- list ----------
// A failed list load says why above the list, with Retry, and keeps what was already shown.
function listProblem(text) {
  const box = $("list-msg");
  if (!text) { box.hidden = true; return; }
  const retry = el("button", "tab", "Retry");
  retry.type = "button";
  retry.addEventListener("click", () => { box.hidden = true; loadList(); });
  box.replaceChildren(el("span", "", text), retry);
  box.hidden = false;
  if (!items.length) $("list").replaceChildren(el("li", "empty", "Recordings could not be loaded."));
  clearTimeout(listTimer); // a check is still running: keep trying, more slowly
  if (items.some((r) => r.status === "queued" || r.status === "running")) listTimer = setTimeout(loadList, 10000);
}

async function loadList() {
  const q = posQuery();
  let data;
  try {
    const res = await api(`/api/recordings${q ? `?${q}` : ""}`);
    if (!res.ok) return listProblem(`The server could not list recordings: ${await responseError(res)}`);
    data = await res.json();
  } catch (err) {
    return listProblem(err instanceof SyntaxError
      ? "The server sent a reply Readback could not read. Is this the Readback server?"
      : "Could not reach the server. Check that Readback is running.");
  }
  if (q !== posQuery()) return; // the filter changed while this was loading
  listProblem("");
  items = data.items;
  renderPositions(data.positions);
  $("register").href = `/api/alerts.csv${q ? `?${q}` : ""}`;
  const k = data.stats;
  $("k-rec").textContent = k.recordings;
  $("k-hours").textContent = k.hours;
  $("k-alerts").textContent = k.alerts;
  $("k-conf").textContent = k.confirmed;
  $("k-false").textContent = k.false_alarm;
  $("k-open").textContent = k.unreviewed;
  $("k-prec").textContent = k.precision == null ? "–" : `${Math.round(k.precision * 100)}%`;
  if (data.retention_days > 0) {
    $("retention").hidden = false;
    $("retention").textContent = `kept ${data.retention_days} days`;
  }
  renderList();
  const busy = items.some((r) => r.status === "queued" || r.status === "running");
  clearTimeout(listTimer);
  if (busy) listTimer = setTimeout(loadList, 2500);
}

function renderList() {
  const list = $("list");
  const focused = list.contains(document.activeElement) && document.activeElement.dataset.id;
  list.replaceChildren();
  if (!items.length) {
    list.append(el("li", "empty", position ? "No recordings for this position." : "No recordings yet."));
    return;
  }
  for (const r of items) {
    const li = el("li", `rec${r.id === selected ? " selected" : ""}`);
    const open = el("button", "rec-open");
    open.type = "button";
    open.dataset.id = r.id;
    if (r.id === selected) open.setAttribute("aria-current", "true");
    open.append(el("span", "name", r.name));
    const sub = el("span", "sub");
    if (r.unit || r.frequency) sub.append(el("span", "pos", posLabel(r)));
    sub.append(el("span", "", fmtDate(r.created)));
    if (r.status === "done") {
      const s = r.summary;
      const judged = (r.verdicts.confirmed || 0) + (r.verdicts.false_alarm || 0);
      sub.append(el("span", "", fmtTime(s.duration)), el("span", "", `${s.alerts} alerts`));
      if (s.alerts - judged > 0) sub.append(el("span", "open", `${s.alerts - judged} to review`));
    } else if (r.status === "failed") {
      sub.append(el("span", "failed", "failed"));
    } else {
      sub.append(el("span", "", r.status === "queued" ? "queued" : `checking ${Math.round(r.progress * 100)}%`));
    }
    open.append(sub);
    open.setAttribute("aria-label", [r.name, ...[...sub.children].map((x) => x.textContent)].join(", "));
    if (r.status === "running" || r.status === "queued") {
      const bar = el("span", "bar-progress");
      const fill = el("i");
      fill.style.width = `${Math.round(r.progress * 100)}%`;
      bar.append(fill);
      open.append(bar);
    }
    open.addEventListener("click", () => {
      select(r.id);
      // On a phone the detail sits under the list, so bring it up or the tap seems to do nothing.
      if (narrow.matches) $("detail").scrollIntoView({ behavior: motion(), block: "start" });
    });
    li.append(open);
    list.append(li);
  }
  if (focused) list.querySelector(`[data-id="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true }); // the list polls
}

// ---------- detail ----------
function select(id) {
  selected = id;
  history.replaceState(null, "", `#${id}`);
  renderList();
  loadDetail(true);
}

function detailProblem(text) {
  const box = el("div", "notice err");
  const retry = el("button", "tab", "Retry");
  retry.type = "button";
  retry.addEventListener("click", () => loadDetail(true));
  box.append(el("span", "", text), retry);
  $("detail").replaceChildren(box);
}

async function loadDetail(fresh = false) {
  clearTimeout(detailTimer);
  if (!selected) return;
  const id = selected;
  let rec;
  try {
    const res = await api(`/api/recordings/${id}`);
    if (id !== selected) return; // another recording was picked while this was loading
    if (res.status === 404) {
      $("detail").replaceChildren(el("div", "empty-detail", "This recording no longer exists."));
      return;
    }
    if (!res.ok) return detailProblem(`Could not load this recording: ${await responseError(res)}`);
    rec = await res.json();
  } catch (err) {
    if (id !== selected) return;
    return detailProblem(`Could not load this recording: ${fetchError(err)}.`);
  }
  if (id !== selected) return;
  const changed = fresh || !detail || detail.id !== rec.id || detail.status !== rec.status;
  detail = rec;
  if (changed) renderDetail();
  else updateProgress();
  if (rec.status === "queued" || rec.status === "running") detailTimer = setTimeout(loadDetail, 2000);
}

function updateProgress() {
  const fill = document.querySelector("#detail .bar-progress > i");
  if (fill) fill.style.width = `${Math.round(detail.progress * 100)}%`;
  const note = document.querySelector("#detail .progress-note");
  if (note) note.textContent = `Checking: ${Math.round(detail.progress * 100)}% of transmissions transcribed.`;
}

function renderDetail() {
  const rec = detail;
  const box = $("detail");
  box.replaceChildren();

  const head = el("div", "detail-head");
  const title = el("div");
  title.append(el("h2", "", rec.name));
  const bits = [fmtDate(rec.created), `parser: ${PARSERS[rec.parser] || rec.parser}`];
  if (rec.unit || rec.frequency) bits.unshift(posLabel(rec));
  if (rec.callsigns) bits.push(`callsigns: ${rec.callsigns}`);
  if (rec.summary) {
    bits.push(fmtTime(rec.summary.duration), `${rec.summary.transmissions} transmissions`);
    if (rec.summary.failed_transmissions) bits.push(`${rec.summary.failed_transmissions} not transcribed`);
    if (rec.summary.failed_parses) bits.push(`${rec.summary.failed_parses} not parsed`);
  }
  title.append(el("div", "meta", bits.join(" · ")));
  const actions = el("div", "actions");
  const back = el("button", "tab back", "Back to recordings");
  back.type = "button";
  back.addEventListener("click", () => {
    $("list").scrollIntoView({ behavior: motion(), block: "start" });
    $("list").querySelector(".rec.selected .rec-open")?.focus({ preventScroll: true });
  });
  actions.append(back);
  if (rec.status === "done") {
    const csv = el("a", "tab", "Export CSV");
    csv.href = `/api/recordings/${rec.id}/alerts.csv`;
    csv.download = "";
    actions.append(csv);
  }
  const edit = el("button", "tab", "Edit");
  edit.type = "button";
  edit.title = "Rename, or file under a unit and frequency";
  edit.addEventListener("click", () => editForm(head));
  actions.append(edit);
  const del = el("button", "tab danger", "Delete");
  del.type = "button";
  del.addEventListener("click", deleteRecording);
  actions.append(del);
  head.append(title, actions);
  box.append(head);

  if (rec.status === "failed") {
    box.append(el("div", "notice err", `The check failed: ${rec.error || "unknown error"}`));
    return;
  }
  if (rec.status !== "done") {
    box.append(el("div", "notice progress-note", rec.status === "queued" ? "Queued." : ""));
    const bar = el("div", "bar-progress");
    bar.style.margin = "0 16px";
    bar.append(el("i"));
    box.append(bar);
    updateProgress();
    return;
  }

  if (rec.audio) {
    const player = el("div", "player");
    const audio = el("audio");
    audio.id = "audio";
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = rec.audio;
    audio.addEventListener("timeupdate", highlightPlaying);
    player.append(audio);
    box.append(player);
  }

  const cols = el("div", "cols");
  const alertsCol = el("section");
  alertsCol.append(el("h3", "", `Alerts (${rec.alerts.length})`));
  if (rec.alerts.length) {
    alertsCol.append(el("p", "keys", "Keys: J / K next and previous · C confirm · F false alarm · R reopen · P play · N note · Space pause. Click an alert to find its transmission."));
    const status = el("p", "keys review-status");
    status.id = "review-status";
    status.setAttribute("role", "status");
    alertsCol.append(status);
  }
  if (!rec.alerts.length) alertsCol.append(el("div", "notice", "No readback problems found in this recording."));
  for (const a of [...rec.alerts].sort((x, y) => x.t - y.t)) alertsCol.append(alertCard(a));
  const txCol = el("section");
  txCol.append(el("h3", "", `Frequency (${rec.transmissions.length})`));
  const ol = el("ol", "list");
  for (const tx of rec.transmissions) ol.append(txLine(tx));
  txCol.append(ol);
  cols.append(alertsCol, txCol);
  box.append(cols);
  focusIdx = -1;
}

function editForm(head) {
  if (head.nextElementSibling?.classList.contains("edit-form")) return head.nextElementSibling.querySelector("input").focus();
  const form = el("form", "edit-form");
  const field = (label, key, max, ph) => {
    const l = el("label", "", label);
    const i = el("input");
    Object.assign(i, { name: key, value: detail[key] || "", maxLength: max, placeholder: ph });
    l.append(i);
    return l;
  };
  const save = el("button", "primary", "Save");
  const cancel = el("button", "tab", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", () => form.remove());
  const msg = el("span", "hint");
  form.append(field("Name", "name", 200, ""), field("Unit", "unit", 100, "VOBL TWR"),
              field("Frequency", "frequency", 40, "118.700"), save, cancel, msg);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(form));
    if (!body.name.trim()) delete body.name;
    save.disabled = true;
    let rec;
    try {
      const res = await api(`/api/recordings/${detail.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await responseError(res));
      rec = await res.json();
    } catch (err) {
      msg.className = "hint err";
      msg.textContent = `Could not save: ${fetchError(err)}`;
      return;
    } finally {
      save.disabled = false;
    }
    Object.assign(detail, { name: rec.name, unit: rec.unit, frequency: rec.frequency });
    renderDetail();
    loadList();
  });
  head.after(form);
  form.querySelector("input").focus();
}

function seek(t) {
  const audio = $("audio");
  if (!audio) return;
  audio.currentTime = Math.max(0, t - 1);
  audio.play().catch(() => {});
}

// What was cleared next to what came back, so the problem reads at a glance.
function expectedHeard(a) {
  if (!a.expected && !a.heard) return null;
  const track = a.source === "track";
  const show = (v) => (!v ? "nothing" : track ? v : describe(a.kind, v));
  const labels = track ? ["Cleared", "Flying"] : a.kind === "callsign" ? ["Sent to", "Read back by"] : ["Cleared", "Read back"];
  const row = el("div", "eh");
  row.append(el("span", "k", labels[0]), el("span", "v", show(a.expected)));
  row.append(el("span", "k", labels[1]), el("span", "v heard", show(a.heard)));
  return row;
}

function seekButton(label, title, name, t) {
  const b = el("button", "seek", `▶ ${label}`);
  b.type = "button";
  b.title = title;
  b.setAttribute("aria-label", name);
  b.addEventListener("click", () => seek(t));
  return b;
}

function alertCard(a) {
  const card = el("div", `ra ${a.severity} v-${a.review.verdict}`);
  card.dataset.index = a.index;
  const meta = el("div", "meta");
  const at = seekButton(fmtTime(a.t), "Play from 9 s before the alert", `Play from 9 s before ${fmtTime(a.t)}`,
                        a.t - 8); // seek() starts 1 s earlier still
  meta.append(at, el("span", "sev", a.severity), el("span", "", `${a.source} · ${a.kind.replaceAll("_", " ")}`));
  meta.append(el("span", `verdict ${a.review.verdict}`, VERDICT_LABEL[a.review.verdict]));
  card.append(meta, el("div", "msg", a.message));
  const eh = expectedHeard(a);
  if (eh) card.append(eh);
  card.addEventListener("click", (e) => {
    if (e.target.closest("button, textarea, a")) return;
    focusAlert(alertCards().indexOf(card), true);
  });

  const row = el("div", "verdicts");
  for (const [verdict, label] of [["confirmed", "Confirm"], ["false_alarm", "False alarm"], ["open", "Reopen"]]) {
    if (verdict === "open" && a.review.verdict === "open") continue;
    const b = el("button", `${verdict}${a.review.verdict === verdict ? " on" : ""}`, label);
    b.type = "button";
    b.addEventListener("click", () => setVerdict(a, verdict, note.value, card));
    row.append(b);
  }
  const rep = el("button", "", a.reported ? "Open report" : "Draft occurrence report");
  rep.type = "button";
  rep.addEventListener("click", () => draftReport(a, rep));
  row.append(rep);
  card.append(row);

  const note = el("textarea");
  note.placeholder = "Note (what happened, what was done)";
  note.maxLength = 4000;
  note.value = a.review.note || "";
  note.addEventListener("change", () => setVerdict(a, a.review.verdict, note.value, card));
  card.append(note);
  if (a.review.updated) {
    card.append(el("div", "by", `${VERDICT_LABEL[a.review.verdict]}${a.review.reviewer ? ` by ${a.review.reviewer}` : ""} · ${fmtDate(a.review.updated)}`));
  }
  return card;
}

async function setVerdict(a, verdict, note, card) {
  if (card.dataset.busy) return; // a second C or F before the first save came back
  card.dataset.busy = "1";
  const buttons = card.querySelectorAll(".verdicts button");
  buttons.forEach((b) => { b.disabled = true; });
  const before = a.review.verdict;
  let error = null;
  try {
    const res = await api(`/api/recordings/${detail.id}/alerts/${a.index}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verdict, note, reviewer: $("reviewer").value.trim() }),
    });
    if (res.ok) a.review = await res.json();
    else error = await responseError(res);
  } catch (err) {
    error = fetchError(err);
  }
  if (error) {
    delete card.dataset.busy;
    buttons.forEach((b) => { b.disabled = false; });
    card.querySelector(".save-err")?.remove();
    card.append(el("p", "hint err save-err", `Could not save the verdict: ${error}`));
    return;
  }
  const fresh = alertCard(a);
  fresh.classList.toggle("current", card.classList.contains("current"));
  card.replaceWith(fresh);
  loadList();
  const stayed = focusIdx < 0 || alertCards()[focusIdx] === fresh; // not moved on with J/K meanwhile
  if (verdict !== "open" && verdict !== before && stayed) nextOpen(fresh);
}

// After a verdict, go on to the next alert still to review, wrapping round to the top.
function nextOpen(card) {
  const cards = alertCards();
  const from = cards.indexOf(card);
  const open = (c) => detail.alerts.find((x) => String(x.index) === c.dataset.index)?.review.verdict === "open";
  const next = [...cards.slice(from + 1), ...cards.slice(0, from)].find(open);
  if (next) focusAlert(cards.indexOf(next));
  else if ($("review-status")) $("review-status").textContent = "All alerts in this recording are reviewed.";
}

function txLine(tx) {
  const li = el("li", "tx");
  li.dataset.t = tx.t;
  if (tx.id) li.dataset.id = tx.id;
  const head = el("div", "head");
  const at = seekButton(fmtTime(tx.t), "Play from here", `Play from ${fmtTime(tx.t)}`, tx.t + 1);
  head.append(at, el("span", `role ${tx.role}`, tx.role === "controller" ? "ATC" : "PILOT"));
  if (tx.callsign_label) head.append(el("span", "cs", tx.callsign_label));
  li.append(head, el("div", "text", tx.text));
  if (tx.items.length) {
    const chips = el("div", "chips");
    for (const it of tx.items) chips.append(el("span", "chip", it.label));
    li.append(chips);
  }
  if (tx.snapped_from) {
    const via = el("div", "via");
    via.append(el("span", "snap", `heard "${tx.snapped_from}", matched to a known callsign`));
    li.append(via);
  }
  return li;
}

function highlightPlaying() {
  const t = $("audio").currentTime;
  let current = null;
  for (const li of document.querySelectorAll("#detail li.tx")) {
    li.classList.remove("playing");
    if (Number(li.dataset.t) <= t) current = li;
  }
  if (current) current.classList.add("playing");
}

async function draftReport(a, btn) {
  btn.disabled = true;
  btn.textContent = a.reported ? "Opening…" : "Investigating…";
  try {
    const res = await api(`/api/report/${detail.id}/${a.index}`, { method: "POST" });
    if (!res.ok) throw new Error(await responseError(res));
    const data = await res.json();
    $("report-body").innerHTML = renderMarkdown(data.markdown);
    const dl = $("report-dl");
    if (dl.href.startsWith("blob:")) URL.revokeObjectURL(dl.href);
    dl.href = URL.createObjectURL(new Blob([data.markdown], { type: "text/markdown" }));
    const cs = (a.callsign || "").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "");
    dl.download = `occurrence-report${cs ? `-${cs}` : ""}-${fmtTime(a.t).replaceAll(":", "")}.md`;
    $("report").showModal();
    a.reported = true;
    btn.textContent = "Open report";
  } catch (err) {
    btn.textContent = `Report failed: ${fetchError(err)}. Retry`;
  } finally {
    btn.disabled = false;
  }
}
$("report-close").addEventListener("click", () => $("report").close());

async function deleteRecording() {
  if (!confirm(`Delete "${detail.name}" with its audio, verdicts and reports? This cannot be undone.`)) return;
  let problem = null;
  try {
    const res = await api(`/api/recordings/${detail.id}`, { method: "DELETE" });
    if (!res.ok) problem = await responseError(res);
  } catch (err) {
    problem = fetchError(err);
  }
  if (problem) return alert(`Could not delete the recording: ${problem}`);
  selected = null;
  detail = null;
  history.replaceState(null, "", "/review");
  $("detail").replaceChildren(el("div", "empty-detail", "Deleted."));
  loadList();
}

// ---------- keyboard: a reviewer can work through a shift's alerts without the mouse ----------
let focusIdx = -1; // position of the current alert card in the alerts column
const alertCards = () => [...document.querySelectorAll("#detail .ra")];

// clicked: the reviewer clicked the card, so the page may scroll to its transmission. From the
// keys only the Frequency list scrolls, which keeps the card in view.
function focusAlert(i, clicked = false) {
  const cards = alertCards();
  if (!cards.length) return;
  focusIdx = Math.max(0, Math.min(cards.length - 1, i));
  cards.forEach((c, j) => c.classList.toggle("current", j === focusIdx));
  if (!clicked) cards[focusIdx].scrollIntoView({ block: "nearest", behavior: motion() });
  if ($("review-status")) $("review-status").textContent = "";
  const a = detail.alerts.find((x) => String(x.index) === cards[focusIdx].dataset.index);
  if (a) locateTx(a, clicked);
}

// Highlight the transmission behind an alert: its ref, otherwise the latest one at or before it.
let locatedTimer = null;
function locateTx(a, scrollPage) {
  const lines = [...document.querySelectorAll("#detail li.tx")];
  let line = a.ref && lines.find((li) => li.dataset.id === a.ref);
  if (!line) for (const li of lines) if (Number(li.dataset.t) <= a.t + 0.01) line = li;
  lines.forEach((li) => li.classList.remove("located"));
  if (!line) return;
  void line.offsetWidth; // restart the highlight when the same line is located twice
  line.classList.add("located");
  clearTimeout(locatedTimer);
  locatedTimer = setTimeout(() => line.classList.remove("located"), 4000);
  const list = line.parentElement;
  if (list.scrollHeight > list.clientHeight + 1) { // the list scrolls on its own (narrower screens)
    const l = list.getBoundingClientRect(), r = line.getBoundingClientRect();
    if (r.top < l.top || r.bottom > l.bottom) {
      list.scrollBy({ top: r.top - l.top - (l.height - r.height) / 2, behavior: motion() });
    }
  }
  if (scrollPage) line.scrollIntoView({ block: "nearest", behavior: motion() });
}

document.addEventListener("keydown", (e) => {
  if (!detail || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, audio") || document.querySelector("dialog[open]")) return;
  // Space presses a focused button, except rows and timestamps, where it plays/pauses as before
  if (e.key === " " && e.target.closest("button, a") && !e.target.closest(".rec-open, .seek")) return;
  const key = e.key.toLowerCase();
  const card = alertCards()[focusIdx];
  const a = card && detail.alerts.find((x) => String(x.index) === card.dataset.index);
  if (key === "j") focusAlert(focusIdx + 1);
  else if (key === "k") focusAlert(focusIdx - 1);
  else if (key === " " && $("audio")) {
    const audio = $("audio");
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  } else if (a && key === "c") setVerdict(a, "confirmed", card.querySelector("textarea").value, card);
  else if (a && key === "f") setVerdict(a, "false_alarm", card.querySelector("textarea").value, card);
  else if (a && key === "r") setVerdict(a, "open", card.querySelector("textarea").value, card);
  else if (a && key === "p") seek(a.t - 8);
  else if (a && key === "n") card.querySelector("textarea").focus();
  else return;
  e.preventDefault();
});

// ---------- upload ----------
$("upload").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("up-file").files[0];
  if (!file) return;
  const msg = $("up-msg");
  const btn = e.submitter || $("upload").querySelector("button");
  btn.disabled = true;
  msg.className = "hint";
  msg.textContent = `Uploading ${file.name} (${(file.size / 2 ** 20).toFixed(1)} MB)…`;
  try {
    const q = new URLSearchParams({
      name: $("up-name").value.trim() || file.name,
      callsigns: $("up-cs").value,
      parser: $("up-parser").value,
      unit: $("up-unit").value.trim(),
      frequency: $("up-freq").value.trim(),
    });
    const res = await api(`/api/recordings?${q}`, { method: "POST", body: file });
    if (!res.ok) throw new Error(await responseError(res));
    const data = await res.json();
    msg.textContent = "Uploaded. Checking in the background; you can leave this page.";
    const keep = [$("up-unit").value, $("up-freq").value]; // the next file is likely the same position
    $("upload").reset();
    [$("up-unit").value, $("up-freq").value] = keep;
    if (position) { // show the new recording even if it is another position's
      const p = JSON.parse(position);
      if (p.unit !== keep[0].trim() || (p.frequency != null && p.frequency !== keep[1].trim())) {
        position = $("pos").value = "";
      }
    }
    await loadList();
    select(data.id);
  } catch (err) {
    msg.className = "hint err";
    msg.textContent = `Upload failed: ${fetchError(err)}`;
  } finally {
    btn.disabled = false;
  }
});

(async () => {
  // No reply from the server is not the same as no API key on it: say which one it is.
  const health = await fetch("/healthz").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!health) {
    $("up-msg").className = "hint err";
    $("up-msg").textContent = "Could not reach the server. Check that Readback is running, then reload.";
  } else {
    if (health.parser === "rules") $("up-parser").value = "rules";
    if (!health.glimmer) $("up-parser").querySelector('[value="glimmer"]').remove();
    if (!health.muse) {
      $("up-msg").className = "hint err";
      $("up-msg").textContent = "Checking recordings needs MODEL_API_KEY on the server (Muse Voice Transcribe). "
        + "Recordings already checked can still be reviewed.";
    }
  }
  await loadList();
  const id = location.hash.slice(1);
  if (id) select(id);
})();
