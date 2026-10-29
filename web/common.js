// Shared by every page. api(): fetch the API, and when the server asks for its access token
// (READBACK_TOKEN), ask the user for it once and keep it as an HttpOnly cookie.
let signingIn = null;

function askToken() {
  if (signingIn) return signingIn;
  signingIn = new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "report login";
    dlg.innerHTML = `
      <form method="dialog" class="login-form">
        <h2>Access token</h2>
        <p>This Readback server is protected. Enter the access token your administrator set.</p>
        <input type="password" name="token" autocomplete="current-password" required>
        <p class="login-error" hidden>Wrong token.</p>
        <button class="primary" type="submit">Sign in</button>
      </form>`;
    document.body.append(dlg);
    const form = dlg.querySelector("form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: form.token.value }),
      });
      if (!res.ok) { dlg.querySelector(".login-error").hidden = false; return; }
      dlg.close();
      dlg.remove();
      signingIn = null;
      resolve();
    });
    dlg.addEventListener("cancel", (e) => e.preventDefault());
    dlg.showModal();
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
