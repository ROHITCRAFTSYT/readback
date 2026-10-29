// Recording review: upload recordings, follow the background checks, and give every alert a
// verdict. Verdicts are stored on the server and make up the unit's alert register (CSV).
const $ = (id) => document.getElementById(id);
const PARSERS = { tagger: "tagger", spark: "Muse Spark", glimmer: "Muse Glimmer", rules: "rules" };
const VERDICT_LABEL = { open: "to review", confirmed: "confirmed", false_alarm: "false alarm" };
let items = [];
let selected = null;
let detail = null;
let listTimer = null;
let detailTimer = null;

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

// ---------- reviewer ----------
try { $("reviewer").value = localStorage.getItem("readback-reviewer") || ""; } catch {}
$("reviewer").addEventListener("change", () => {
  try { localStorage.setItem("readback-reviewer", $("reviewer").value.trim()); } catch {}
});

// ---------- list ----------
async function loadList() {
  const res = await api("/api/recordings");
  if (!res.ok) return;
  const data = await res.json();
  items = data.items;
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
  list.replaceChildren();
  if (!items.length) { list.append(el("li", "empty", "No recordings yet.")); return; }
  for (const r of items) {
    const li = el("li", `rec${r.id === selected ? " selected" : ""}`);
    li.append(el("div", "name", r.name));
    const sub = el("div", "sub");
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
    li.append(sub);
    if (r.status === "running" || r.status === "queued") {
      const bar = el("div", "bar-progress");
      const fill = el("i");
      fill.style.width = `${Math.round(r.progress * 100)}%`;
      bar.append(fill);
      li.append(bar);
    }
    li.addEventListener("click", () => select(r.id));
    list.append(li);
  }
}

// ---------- detail ----------
function select(id) {
  selected = id;
  history.replaceState(null, "", `#${id}`);
  renderList();
  loadDetail(true);
}

async function loadDetail(fresh = false) {
  clearTimeout(detailTimer);
  if (!selected) return;
  const res = await api(`/api/recordings/${selected}`);
  if (!res.ok) {
    $("detail").replaceChildren(el("div", "empty-detail", "This recording no longer exists."));
    return;
  }
  const rec = await res.json();
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
  if (rec.callsigns) bits.push(`callsigns: ${rec.callsigns}`);
  if (rec.summary) {
    bits.push(fmtTime(rec.summary.duration), `${rec.summary.transmissions} transmissions`);
    if (rec.summary.failed_transmissions) bits.push(`${rec.summary.failed_transmissions} not transcribed`);
  }
  title.append(el("div", "meta", bits.join(" · ")));
  const actions = el("div", "actions");
  if (rec.status === "done") {
    const csv = el("a", "tab", "Export CSV");
    csv.href = `/api/recordings/${rec.id}/alerts.csv`;
    csv.download = "";
    actions.append(csv);
  }
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
    alertsCol.append(el("p", "keys", "Keys: J / K next and previous · C confirm · F false alarm · R reopen · P play · N note · Space pause"));
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

function seek(t) {
  const audio = $("audio");
  if (!audio) return;
  audio.currentTime = Math.max(0, t - 1);
  audio.play().catch(() => {});
}

function alertCard(a) {
  const card = el("div", `ra ${a.severity} v-${a.review.verdict}`);
  card.dataset.index = a.index;
  const meta = el("div", "meta");
  const at = el("span", "seek", `▶ ${fmtTime(a.t)}`);
  at.title = "Play from here";
  at.addEventListener("click", () => seek(a.t - 8));
  meta.append(at, el("span", "sev", a.severity), el("span", "", `${a.source} · ${a.kind.replaceAll("_", " ")}`));
  meta.append(el("span", `verdict ${a.review.verdict}`, VERDICT_LABEL[a.review.verdict]));
  card.append(meta, el("div", "msg", a.message));

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
  card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  const res = await api(`/api/recordings/${detail.id}/alerts/${a.index}/review`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ verdict, note, reviewer: $("reviewer").value.trim() }),
  });
  if (res.ok) {
    a.review = await res.json();
    const fresh = alertCard(a);
    fresh.classList.toggle("current", card.classList.contains("current"));
    card.replaceWith(fresh);
    loadList();
  } else {
    card.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    alert(`Could not save the verdict: ${(await res.json()).detail || res.statusText}`);
  }
}

function txLine(tx) {
  const li = el("li", "tx");
  li.dataset.t = tx.t;
  const head = el("div", "head");
  const at = el("span", "seek", `▶ ${fmtTime(tx.t)}`);
  at.addEventListener("click", () => seek(tx.t + 1));
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
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    $("report-body").innerHTML = renderMarkdown(data.markdown);
    const dl = $("report-dl");
    if (dl.href.startsWith("blob:")) URL.revokeObjectURL(dl.href);
    dl.href = URL.createObjectURL(new Blob([data.markdown], { type: "text/markdown" }));
    $("report").showModal();
    a.reported = true;
    btn.textContent = "Open report";
  } catch (err) {
    btn.textContent = `Report failed: ${err.message}. Retry`;
  } finally {
    btn.disabled = false;
  }
}
$("report-close").addEventListener("click", () => $("report").close());

async function deleteRecording() {
  if (!confirm(`Delete "${detail.name}" with its audio, verdicts and reports? This cannot be undone.`)) return;
  const res = await api(`/api/recordings/${detail.id}`, { method: "DELETE" });
  if (!res.ok) return;
  selected = null;
  detail = null;
  history.replaceState(null, "", "/review");
  $("detail").replaceChildren(el("div", "empty-detail", "Deleted."));
  loadList();
}

// ---------- keyboard: a reviewer can work through a shift's alerts without the mouse ----------
let focusIdx = -1; // position of the current alert card in the alerts column
const alertCards = () => [...document.querySelectorAll("#detail .ra")];

function focusAlert(i) {
  const cards = alertCards();
  if (!cards.length) return;
  focusIdx = Math.max(0, Math.min(cards.length - 1, i));
  cards.forEach((c, j) => c.classList.toggle("current", j === focusIdx));
  cards[focusIdx].scrollIntoView({ block: "nearest", behavior: "smooth" });
}

document.addEventListener("keydown", (e) => {
  if (!detail || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, button, audio") || document.querySelector("dialog[open]")) return;
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
    });
    const res = await api(`/api/recordings?${q}`, { method: "POST", body: file });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    msg.textContent = "Uploaded. Checking in the background; you can leave this page.";
    $("upload").reset();
    await loadList();
    select(data.id);
  } catch (err) {
    msg.className = "hint err";
    msg.textContent = `Upload failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
});

(async () => {
  const health = await fetch("/healthz").then((r) => r.json()).catch(() => ({}));
  if (health.parser === "rules") $("up-parser").value = "rules";
  if (!health.glimmer) $("up-parser").querySelector('[value="glimmer"]').remove();
  if (!health.muse) {
    $("up-msg").className = "hint err";
    $("up-msg").textContent = "Checking recordings needs MODEL_API_KEY on the server (Voice Transcribe).";
  }
  await loadList();
  const id = location.hash.slice(1);
  if (id) select(id);
})();
