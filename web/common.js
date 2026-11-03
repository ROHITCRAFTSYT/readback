// Shared by every page: the sidebar (navigation, model status, UTC clock), and api(), which
// fetches the API and, when the server asks for its access token (READBACK_TOKEN), asks the
// user for it once and keeps it as an HttpOnly cookie.
const ICONS = {
  monitor: '<circle cx="8" cy="8" r="6.2"/><circle cx="8" cy="8" r="2.6"/><path d="M8 8l4.2-4.2"/>',
  review: '<path d="M4 2.5h8v11H4z"/><path d="M6 6l1.4 1.4L10 4.8M6 10.5h4"/>',
  training: '<path d="M2 13.5h12M3.5 11l3-3.5 2.5 2 4-5"/>',
  api: '<path d="M5.5 4L2 8l3.5 4M10.5 4L14 8l-3.5 4"/>',
};
const PAGES = [
  ["/", "Monitor", "monitor"],
  ["/review", "Review", "review"],
  ["/training", "Training", "training"],
  ["/docs", "API", "api"],
];

function renderNav() {
  const nav = document.getElementById("nav");
  if (!nav) return;
  const here = location.pathname.replace(/\/$/, "") || "/";
  const links = PAGES.map(([href, label, icon]) => {
    const on = (href === "/" ? here === "/" : here.startsWith(href)) ? ' aria-current="page"' : "";
    return `<a href="${href}"${on}><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[icon]}</svg>${label}</a>`;
  }).join("");
  nav.innerHTML = `
    <a class="brand" href="/">
      <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
        <circle cx="15" cy="15" r="13.5" fill="#101a26" stroke="#2c4a6b"/>
        <circle cx="15" cy="15" r="8" fill="none" stroke="#2c4a6b"/>
        <path d="M15 15L24.5 5.5A13.5 13.5 0 0 1 28.5 15Z" fill="rgba(77,163,255,.35)"/>
        <path d="M15 15L24.5 5.5" stroke="#4da3ff" stroke-width="1.6"/>
        <rect x="18.5" y="17.5" width="3.4" height="3.4" fill="none" stroke="#f0524f" stroke-width="1.3"/>
      </svg>
      <span><b>Readback</b><small>ATC readback assurance</small></span>
    </a>
    <div class="nav-links"><div class="nav-label">Workspace</div>${links}</div>
    <div class="nav-status">
      <div><span class="utc" id="utc">--:--</span><small class="clock-label"> UTC</small></div>
      <div class="row"><span><i class="dot" data-health="muse"></i>Voice Transcribe</span><span class="state" data-health-state="muse"></span></div>
      <div class="row"><span><i class="dot" data-health="spark"></i>Muse Spark</span><span class="state" data-health-state="spark"></span></div>
      <div class="row"><span><i class="dot" data-health="glimmer"></i>Muse Glimmer (fine-tuned)</span><span class="state" data-health-state="glimmer"></span></div>
      <div class="row"><span><i class="dot on"></i>Tagger (on-prem)</span><span class="state">CPU</span></div>
      <p class="nav-note">Decision support for review. Every alert needs a human verdict.</p>
    </div>`;
  const tick = () => {
    const el = document.getElementById("utc");
    if (el) el.textContent = new Date().toISOString().slice(11, 16);
  };
  tick();
  setInterval(tick, 5000);
  const show = (state, text) => {
    for (const [k, on] of Object.entries(state)) {
      nav.querySelector(`[data-health="${k}"]`).className = `dot ${on ? "on" : "off"}`;
      nav.querySelector(`[data-health-state="${k}"]`).textContent = text || (on ? "ready" : "off");
    }
  };
  fetch("/healthz").then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText)))).then((h) => {
    show({ muse: h.muse, spark: h.muse, glimmer: h.glimmer });
  }).catch(() => show({ muse: false, spark: false, glimmer: false }, "unreachable"));
}
renderNav();

let signingIn = null;

function askToken() {
  if (signingIn) return signingIn;
  signingIn = new Promise((resolve) => {
    const back = document.activeElement; // focus returns here once signed in
    const dlg = document.createElement("dialog");
    dlg.className = "report login";
    dlg.setAttribute("aria-labelledby", "login-title");
    dlg.setAttribute("aria-describedby", "login-why");
    dlg.innerHTML = `
      <form method="dialog" class="login-form">
        <h2 id="login-title">Access token</h2>
        <p id="login-why">This Readback server is protected. Enter the access token your administrator set.</p>
        <input type="password" name="token" aria-label="Access token" autocomplete="current-password" required autofocus>
        <p class="login-error" role="alert" hidden></p>
        <button class="primary" type="submit">Sign in</button>
      </form>`;
    document.body.append(dlg);
    const form = dlg.querySelector("form");
    const input = form.token;
    const error = dlg.querySelector(".login-error");
    const fail = (text) => {
      error.textContent = text;
      error.hidden = false;
      input.setAttribute("aria-invalid", "true");
      input.select();
      input.focus();
    };
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button");
      if (btn.disabled) return;
      btn.disabled = true;
      let res;
      try {
        res = await fetch("/api/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: input.value }),
        });
      } catch {
        res = null;
      }
      btn.disabled = false;
      if (!res) return fail("Could not reach the server. Try again.");
      if (res.status === 401) return fail("Wrong token.");
      if (!res.ok) return fail(`Sign-in failed: ${await responseError(res)}`);
      dlg.close();
      dlg.remove();
      signingIn = null;
      if (back && back.isConnected && back.focus) back.focus();
      resolve();
    });
    dlg.addEventListener("cancel", (e) => e.preventDefault());
    dlg.showModal();
    input.focus();
  });
  return signingIn;
}

async function api(url, opts = {}) {
  let res = await fetch(url, opts);
  if (res.status === 401) {
    await askToken();
    res = await fetch(url, opts);
  }
  return res;
}

// What went wrong with a response that is not ok: the server's {detail}, or the start of its
// body when that is not JSON (a proxy's error page, say), or else the status line.
async function responseError(res) {
  let text = "";
  try { text = await res.text(); } catch {}
  try {
    const detail = JSON.parse(text).detail;
    if (detail) return typeof detail === "string" ? detail : JSON.stringify(detail);
  } catch {}
  const plain = text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  if (plain && plain.length <= 200) return plain;
  return `${res.status} ${res.statusText || "error"}`.trim();
}

// The message for an api() call that threw: fetch throws a TypeError when it gets no reply.
function fetchError(err) {
  return err instanceof TypeError ? "could not reach the server" : err.message;
}

// Occurrence reports arrive as Markdown; this renders the subset the agent writes.
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function inline(s) {
  return escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|\s)\*(.+?)\*(?=\s|$)/g, "$1<em>$2</em>");
}
function renderMarkdown(md) {
  const out = [];
  let list = false;
  for (const line of md.split("\n")) {
    if (line.startsWith("- ")) {
      if (!list) { out.push("<ul>"); list = true; }
      out.push(`<li>${inline(line.slice(2))}</li>`);
      continue;
    }
    if (list) { out.push("</ul>"); list = false; }
    if (line.startsWith("# ")) out.push(`<h1>${inline(line.slice(2))}</h1>`);
    else if (line.startsWith("## ")) out.push(`<h3>${inline(line.slice(3))}</h3>`);
    else if (line.startsWith("*") && line.endsWith("*")) out.push(`<p class="muted">${inline(line)}</p>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}
