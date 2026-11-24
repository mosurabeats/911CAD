/* Interim CAD - browser client. No external dependencies. All text is inserted via textContent (no innerHTML). */
"use strict";

const S = {
  me: null, ref: null, board: { calls: [], units: [] }, seq: null, view: "board",
  callId: null, detail: null, pollTimer: null, lastActivity: Date.now(), connOk: true,
};

// ---------------------------------------------------------------- helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v; // CSSOM is allowed under the strict CSP
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
const DEMO = window.CAD_DEMO || null; // set only by the browser-only demo build
function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (_) { /* storage blocked: fine */ }
  return null;
}
// In-page dialogs (browser prompt()/confirm() are blocked in some kiosk and embedded browsers).
function ask(title, { input = null, okLabel = "OK", danger = false } = {}) {
  return new Promise((resolve) => {
    const field_ = input ? h("input", { type: input.type || "text", value: "", autocomplete: "off", required: true }) : null;
    const done = (v) => { closeModal(); resolve(v); };
    const f = h("form", { onsubmit: (e) => { e.preventDefault(); done(input ? field_.value.trim() || null : true); } },
      h("h2", {}, title),
      input ? h("label", {}, input.label, field_) : null,
      h("div", { class: "row", style: "margin-top:12px" },
        h("button", { class: danger ? "danger" : "primary" }, okLabel),
        h("button", { type: "button", onclick: () => done(input ? null : false) }, "Cancel")));
    openModal(f);
  });
}
const pad = (n) => String(n).padStart(2, "0");
function fmt(iso, withDate = true) {
  if (!iso) return "";
  const d = new Date(iso);
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return withDate ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${t}` : t;
}
function fmtShort(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date();
  return d.toDateString() === today.toDateString() ? fmt(iso, false) : `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function elapsed(iso) {
  const m = Math.floor((Date.now() - new Date(iso)) / 60000);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${pad(m % 60)}`;
}
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fromLocalInput(v) { return v ? new Date(v).toISOString() : null; }
const can = (p) => S.me && S.me.permissions.includes(p);
const label = (s) => String(s || "").replace(/_/g, " ");
function toast(msg, err = false) {
  const m = h("div", { class: "msg" + (err ? " err" : "") }, msg);
  $("#toast").append(m);
  setTimeout(() => m.remove(), err ? 6000 : 3000);
}
function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.type === "datetime-local") out[el.name] = fromLocalInput(el.value);
    else out[el.name] = el.value;
  }
  return out;
}
function field(lbl, name, value, opts = {}) {
  let input;
  if (opts.options) {
    input = h("select", { name, required: opts.required },
      opts.blank !== false ? h("option", { value: "" }, opts.blank || "") : null,
      opts.options.map((o) => {
        const [v, t] = Array.isArray(o) ? o : [o, label(o)];
        return h("option", { value: v, selected: String(v) === String(value ?? "") }, t);
      }));
  } else if (opts.textarea) {
    input = h("textarea", { name, rows: opts.rows || 3, maxlength: opts.max }, value ?? "");
  } else {
    input = h("input", {
      name, type: opts.type || "text", value: opts.type === "datetime-local" ? toLocalInput(value) : (value ?? ""),
      required: opts.required, maxlength: opts.max, placeholder: opts.placeholder, autocomplete: opts.autocomplete || "off",
      step: opts.step, inputmode: opts.inputmode,
    });
  }
  return h("label", { class: opts.cls }, lbl, input);
}

// ---------------------------------------------------------------- API
async function api(method, path, body, opts = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (S.me && S.me.csrf) headers["X-CSRF-Token"] = S.me.csrf;
  if (opts.background) headers["X-Background"] = "1";
  let res;
  try {
    res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, credentials: "same-origin" });
  } catch (e) {
    setConn(false);
    throw new Error("Cannot reach the CAD server. Check the network. Use paper logs until it is back.");
  }
  setConn(true);
  let data = {};
  try { data = await res.json(); } catch (_) { /* ignore */ }
  if (res.status === 401 && path !== "/api/login") {
    stopPolling();
    S.me = null;
    renderLogin("Your session ended (signed out or inactive). Please sign in again.");
    throw new Error("Not signed in");
  }
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}
function setConn(ok) {
  S.connOk = ok;
  const dot = $("#conn");
  if (dot) { dot.classList.toggle("bad", !ok); dot.title = ok ? "Connected" : "Connection lost"; }
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); await refreshAll(); return r; }
  catch (e) { if (e.message !== "Not signed in") toast(e.message, true); return null; }
}

// ---------------------------------------------------------------- auth screens
function renderLogin(message) {
  closeDrawer();
  const app = $("#app");
  let needCode = false;
  const err = h("div", { class: "error" }, message || "");
  const codeField = h("label", { style: "display:none" }, "Authenticator code",
    h("input", { name: "totp", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6 }));
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      const d = formData(form);
      try {
        const r = await api("POST", "/api/login", { username: d.username, password: d.password, totp: needCode ? d.totp : undefined });
        if (r.mfa_required) {
          needCode = true; codeField.style.display = ""; codeField.querySelector("input").focus();
          return;
        }
        await boot();
      } catch (ex) { err.textContent = ex.message; }
    },
  },
    field("Username", "username", "", { required: true, autocomplete: "username" }),
    h("label", {}, "Password", h("input", { name: "password", type: "password", required: true, autocomplete: "current-password" })),
    codeField,
    h("button", { class: "primary", type: "submit" }, "Sign in"),
    err);
  app.replaceChildren(h("div", { class: "login" },
    h("h1", {}, "Interim CAD"),
    h("div", { class: "muted" }, "Authorized users only"),
    form,
    DEMO && DEMO.loginHint ? h("div", { class: "sub" }, DEMO.loginHint.map((line) => h("div", {}, line))) : null,
    h("div", { class: "banner" },
      "This system contains law enforcement and criminal justice information. Access is restricted to authorized personnel " +
      "for official purposes. All activity is logged and monitored. Unauthorized access or use is prohibited and may be prosecuted.")));
  setTimeout(() => form.username.focus(), 0);
}

function renderSetup() {
  const app = $("#app");
  const parts = [];
  if (S.me.must_change_pw) {
    const err = h("div", { class: "error" });
    const f = h("form", {
      onsubmit: async (e) => {
        e.preventDefault();
        const d = formData(f);
        if (d.new_password !== d.confirm) { err.textContent = "Passwords do not match."; return; }
        try { await api("POST", "/api/me/password", { current_password: d.current_password, new_password: d.new_password }); toast("Password changed"); await boot(); }
        catch (ex) { err.textContent = ex.message; }
      },
    },
      h("p", { class: "muted" }, `Choose a new password (at least ${S.me.password_min_length} characters, mixing upper/lowercase, digits, symbols — or a long passphrase).`),
      h("label", {}, "Current (temporary) password", h("input", { type: "password", name: "current_password", required: true, autocomplete: "current-password" })),
      h("label", {}, "New password", h("input", { type: "password", name: "new_password", required: true, autocomplete: "new-password" })),
      h("label", {}, "Confirm new password", h("input", { type: "password", name: "confirm", required: true, autocomplete: "new-password" })),
      h("button", { class: "primary" }, "Change password"), err);
    parts.push(h("h1", {}, "Change your password"), f);
  } else if (S.me.mfa_setup_required) {
    parts.push(h("h1", {}, "Set up two-factor sign-in"), mfaSetupBlock(async () => { await boot(); }));
  }
  parts.push(h("div", { class: "banner" }, h("button", { onclick: logout }, "Sign out")));
  app.replaceChildren(h("div", { class: "login" }, parts));
}

function mfaSetupBlock(done) {
  const box = h("div", {});
  const start = h("button", {
    class: "primary", onclick: async () => {
      try {
        const r = await api("POST", "/api/me/mfa/begin");
        const err = h("div", { class: "error" });
        const f = h("form", {
          onsubmit: async (e) => {
            e.preventDefault();
            try { await api("POST", "/api/me/mfa/confirm", { code: f.code.value }); toast("Two-factor sign-in enabled"); await done(); }
            catch (ex) { err.textContent = ex.message; }
          },
        },
          h("p", {}, "1. Open an authenticator app (Microsoft Authenticator, Google Authenticator, Authy, etc.) and add an account manually using this key:"),
          h("div", { class: "code" }, r.secret.replace(/(.{4})/g, "$1 ").trim()),
          h("p", { class: "muted" }, "Type: time-based, 6 digits, 30 seconds. (Setup link: ", h("span", { class: "code" }, r.uri), ")"),
          h("p", {}, "2. Enter the 6-digit code the app shows:"),
          h("input", { name: "code", inputmode: "numeric", maxlength: 6, required: true, autocomplete: "one-time-code" }),
          h("button", { class: "primary", style: "margin-top:8px" }, "Verify and enable"), err);
        box.replaceChildren(f);
        f.code.focus();
      } catch (ex) { toast(ex.message, true); }
    },
  }, "Begin setup");
  box.append(h("p", { class: "muted" }, "Your agency requires a second factor. You'll need an authenticator app on your phone."), start);
  return box;
}

async function logout() {
  try { await api("POST", "/api/logout"); } catch (_) { /* ignore */ }
  stopPolling(); S.me = null; S.shownLogon = false; renderLogin("Signed out.");
}

// ---------------------------------------------------------------- boot & polling
async function boot() {
  try {
    S.me = await api("GET", "/api/me");
  } catch (e) {
    if (e.message !== "Not signed in") renderLogin(e.message);
    return;
  }
  if (S.me.must_change_pw || S.me.mfa_setup_required) { renderSetup(); return; }
  S.ref = await api("GET", "/api/reference");
  S.seq = null;
  renderShell();
  const pl = S.me.previous_logon;
  if (pl && !S.shownLogon) {
    S.shownLogon = true;
    toast(pl.at ? `Last sign-in: ${fmt(pl.at)} from ${pl.ip || "unknown"}.` : "This is your first sign-in.");
    if (pl.failed_since) toast(`${pl.failed_since} failed sign-in attempt(s) on your account since then. If that wasn't you, tell a supervisor.`, true);
  }
  await refreshBoard();
  startPolling();
}
function startPolling() {
  stopPolling();
  S.pollTimer = setInterval(async () => {
    try {
      await refreshBoard(true);
      if (S.callId && S.detailSeq !== S.seq) await loadDetail(true);
    } catch (_) { /* conn state shown */ }
  }, 3000);
}
function stopPolling() { if (S.pollTimer) clearInterval(S.pollTimer); S.pollTimer = null; }
async function refreshBoard(background = false) {
  const r = await api("GET", "/api/board" + (S.seq !== null ? `?since=${S.seq}` : ""), undefined, { background });
  if (r.changed === false) return;
  S.seq = r.seq;
  S.board = r;
  if (S.view === "board") renderBoard();
  renderOfficerBar();
}
async function refreshAll() {
  S.seq = null;
  await refreshBoard();
  if (S.callId) await loadDetail();
}
// Activity tracking: background polls do not extend the session; real input does.
["keydown", "mousedown", "touchstart"].forEach((ev) => document.addEventListener(ev, () => {
  const now = Date.now();
  if (S.me && now - S.lastActivity > 60000) { S.lastActivity = now; api("GET", "/api/me").catch(() => {}); }
  S.lastActivity = now;
}, { passive: true }));

// ---------------------------------------------------------------- shell
function renderShell() {
  const views = [["board", "Board"], ["search", "Search"]];
  if (can("admin") || can("unit.manage")) views.push(["admin", "Admin"]);
  if (can("audit")) views.push(["audit", "Audit"]);
  if (can("export")) views.push(["export", "Export"]);
  const nav = h("nav", {}, views.map(([v, t]) => h("button", { class: S.view === v ? "active" : "", onclick: () => setView(v) }, t)));
  const clock = h("span", { class: "clock", id: "clock" });
  const theme = store("cad-theme") || (DEMO ? null : "dark");
  if (theme) document.documentElement.dataset.theme = theme;
  const header = h("header", { class: "top" },
    h("span", { class: "brand" }, S.me.agency.name, " CAD"),
    can("call.create") ? h("button", { class: "primary", onclick: newCallModal, title: "F2" }, "+ New Call (F2)") : null,
    nav,
    h("span", { class: "grow" }),
    h("span", { id: "conn", class: "conn", title: "Connected" }),
    clock,
    h("span", { class: "muted hide-sm" }, `${S.me.user.full_name} (${S.me.user.role})`),
    h("button", { class: "small", onclick: accountModal }, "Account"),
    h("button", { class: "small", title: "Toggle light/dark", onclick: () => {
      const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
      const t = cur === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = t; store("cad-theme", t);
    } }, "◐"),
    h("button", { class: "small", onclick: logout }, "Sign out"));
  const officerBar = h("div", { id: "officerbar" });
  const main = h("main", { id: "main" });
  $("#app").replaceChildren(h("div", { class: "shell" }, header, officerBar, main));
  tickClock();
  renderView();
}
function tickClock() {
  const c = $("#clock");
  if (c) c.textContent = fmt(new Date().toISOString());
}
setInterval(() => {
  tickClock();
  document.querySelectorAll("[data-elapsed]").forEach((el) => {
    el.textContent = elapsed(el.dataset.elapsed);
  });
}, 1000);
function setView(v) { S.view = v; renderShell(); if (S.detail) renderDrawer(); }
function renderView() {
  const main = $("#main");
  if (!main) return;
  if (S.view === "board") renderBoard();
  else if (S.view === "search") main.replaceChildren(searchView());
  else if (S.view === "admin") main.replaceChildren(adminView());
  else if (S.view === "audit") main.replaceChildren(auditView());
  else if (S.view === "export") main.replaceChildren(exportView());
}

// ---------------------------------------------------------------- officer bar
function renderOfficerBar() {
  const bar = $("#officerbar");
  if (!bar || S.me.user.role !== "officer") return;
  const mine = S.board.units.find((u) => u.officer_user_id === S.me.user.id);
  if (!mine) {
    const sel = h("select", {}, h("option", { value: "" }, "Select your unit…"),
      S.board.units.map((u) => h("option", { value: u.id }, `${u.unit_code}${u.officer_name ? " (" + u.officer_name + ")" : ""}`)));
    bar.replaceChildren(h("div", { class: "officerbar" }, h("strong", {}, "Not signed on to a unit."), sel,
      h("button", { class: "primary", onclick: () => sel.value && act(() => api("POST", `/api/units/${sel.value}/signon`, {}), "Signed on") }, "Sign on")));
    return;
  }
  bar.replaceChildren(h("div", { class: "officerbar" },
    h("strong", {}, `My unit: ${mine.unit_code}`),
    h("span", { class: `st t-${mine.status}` }, label(mine.status)),
    mine.current_call_number ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); openCall(mine.current_call_id); } }, `${mine.current_call_number} ${mine.current_call_type || ""}`) : null,
    unitButtons(mine),
    h("span", { class: "grow" }),
    h("button", { class: "small", onclick: () => act(() => api("POST", `/api/units/${mine.id}/signoff`, {}), "Signed off") }, "Sign off unit")));
}

// ---------------------------------------------------------------- board
function renderBoard() {
  const main = $("#main");
  if (!main || S.view !== "board") return;
  const calls = S.board.calls || [];
  const open = calls.filter((c) => c.status !== "CLOSED");
  const closed = calls.filter((c) => c.status === "CLOSED");
  const callRow = (c) => h("tr", { class: `click st-${c.status}` + (c.id === S.callId ? " selected" : ""), onclick: () => openCall(c.id) },
    h("td", {}, h("span", { class: `prio prio-${c.priority}` }, c.priority)),
    h("td", { class: "mono" }, c.call_number),
    h("td", {}, h("span", { class: `badge b-${c.status}` }, c.status)),
    h("td", {}, c.call_type),
    h("td", {}, [c.location_address || c.location_common || "", c.location_city ? `, ${c.location_city}` : ""]),
    h("td", { class: "mono" }, c.units || (c.status === "PENDING" ? h("span", { class: "error" }, "—") : "")),
    h("td", { class: "mono hide-sm" }, fmtShort(c.received_at)),
    h("td", { class: "hide-sm" }, c.status === "CLOSED" ? (c.disposition || "") :
      h("span", { class: "elapsed" + (c.status === "PENDING" && (Date.now() - new Date(c.received_at)) > 5 * 60000 ? " late" : ""), "data-elapsed": c.received_at }, elapsed(c.received_at))));
  const head = h("tr", {}, ["P", "Call #", "Status", "Type", "Location", "Units", "Received", "Age/Disp"].map((t, i) => h("th", { class: i >= 6 ? "hide-sm" : "" }, t)));
  const pending = open.filter((c) => c.status === "PENDING").length;
  const callsPanel = h("section", { class: "panel" },
    h("div", { class: "head" }, h("h2", {}, "Calls"),
      h("span", { class: "muted" }, `${open.length} open`, pending ? h("span", { class: "error" }, ` · ${pending} pending`) : "")),
    h("div", { class: "body" },
      h("table", {}, h("thead", {}, head), h("tbody", {}, open.length ? open.map(callRow) : h("tr", {}, h("td", { colspan: 8, class: "muted" }, "No open calls.")))),
      h("h3", { style: "padding: 0 8px" }, `Closed — last ${S.board.closed_window_hours || 12} hours`),
      h("table", {}, h("tbody", {}, closed.length ? closed.map(callRow) : h("tr", {}, h("td", { class: "muted" }, "None."))))));
  const units = S.board.units || [];
  const unitsPanel = h("section", { class: "panel" },
    h("div", { class: "head" }, h("h2", {}, "Units"),
      h("span", { class: "muted" }, `${units.filter((u) => u.status === "AVAILABLE").length} available`)),
    h("div", { class: "body" }, h("div", { class: "units" }, units.length ? units.map(unitCard) :
      h("div", { class: "muted" }, can("unit.manage") ? "No units yet. Add them under Admin → Units." : "No units configured."))));
  main.replaceChildren(h("div", { class: "board" }, callsPanel, unitsPanel));
}

function unitCard(u) {
  return h("div", { class: `unit us-${u.status}` },
    h("div", { class: "top" },
      h("span", { class: "ucode" }, u.unit_code),
      h("span", { class: `st t-${u.status}` }, label(u.status)),
      h("span", { class: "grow" }),
      h("span", { class: "muted mono", style: "font-size:11px" }, u.status_at ? fmtShort(u.status_at) : "")),
    h("div", { class: "who" }, u.officer_name || h("i", {}, "no crew"),
      can("unit.crew") ? h("button", { class: "small", style: "margin-left:6px", onclick: () => crewModal(u) }, "crew") : null),
    u.current_call_id ? h("div", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); openCall(u.current_call_id); } },
      `${u.current_call_number} ${u.current_call_type || ""} ${u.current_call_address || ""}`)) : null,
    can("unit.status.any") ? unitButtons(u) : null);
}

const STATUS_BTNS = [["DISPATCHED", "DISP"], ["ENROUTE", "ENR"], ["ONSCENE", "ONS"], ["TRANSPORT", "TRN"], ["AVAILABLE", "AVL"], ["BUSY", "BUSY"], ["OUT_OF_SERVICE", "OOS"], ["OFF_DUTY", "OFF"]];
function unitButtons(u) {
  const selectedOpen = S.callId && S.detail && S.detail.call.status !== "CLOSED" ? S.detail.call : null;
  return h("div", { class: "btns" }, STATUS_BTNS.map(([st, t]) => {
    if (st === "DISPATCHED" && !can("unit.status.any")) return null;
    const needsCall = ["DISPATCHED", "ENROUTE", "ONSCENE", "TRANSPORT"].includes(st);
    let callId = null;
    let title = label(st);
    if (needsCall) {
      if (st === "DISPATCHED" || !u.current_call_id) {
        if (!selectedOpen) return h("button", { disabled: true, title: "Open a call first" }, t);
        callId = selectedOpen.id; title = `${label(st)} to ${selectedOpen.call_number}`;
      }
    }
    return h("button", { title, class: u.status === st ? "primary" : "",
      onclick: () => act(() => api("POST", `/api/units/${u.id}/status`, { status: st, call_id: callId })) }, t);
  }));
}

function crewModal(u) {
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      const r = await act(() => api("POST", `/api/units/${u.id}/signon`, { officer_name: f.officer_name.value }), "Crew updated");
      if (r) closeModal();
    },
  },
    h("h2", {}, `Crew for ${u.unit_code}`),
    field("Officer name(s) / badge", "officer_name", u.officer_name, { max: 100 }),
    h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "primary" }, "Save"),
      h("button", { type: "button", onclick: closeModal }, "Cancel"),
      h("span", { class: "grow" }),
      h("button", { type: "button", class: "danger", onclick: async () => { await act(() => api("POST", `/api/units/${u.id}/signoff`, {}), "Unit off duty"); closeModal(); } }, "Sign off / off duty")));
  openModal(f);
}

// ---------------------------------------------------------------- modal
function openModal(content) {
  closeModal();
  const bg = h("div", { class: "modal-bg", id: "modal", onmousedown: (e) => { if (e.target === bg) closeModal(); } }, h("div", { class: "modal" }, content));
  document.body.append(bg);
  const first = bg.querySelector("input, select, textarea");
  if (first) first.focus();
}
function closeModal() { const m = $("#modal"); if (m) m.remove(); }

// ---------------------------------------------------------------- new call
function callTypeOptions() {
  return S.ref.call_types.filter((t) => t.active).map((t) => [t.code, `${t.code} — ${t.description}`]);
}
function newCallModal() {
  if (!can("call.create")) return;
  const lastCity = store("cad-city") || "";
  const avail = S.board.units.filter((u) => u.status === "AVAILABLE");
  const err = h("div", { class: "error" });
  const backEntry = h("details", {}, h("summary", { class: "muted" }, "Back-enter a call from a paper log (set original received time)"),
    h("div", { class: "grid", style: "margin-top:6px" }, field("Received at (local time)", "received_at", "", { type: "datetime-local", cls: "wide" })));
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      const d = formData(f);
      d.assign_units = avail.filter((u) => d["u_" + u.id]).map((u) => u.id);
      for (const u of avail) delete d["u_" + u.id];
      if (!d.priority) delete d.priority;
      try {
        const r = await api("POST", "/api/calls", d);
        store("cad-city", d.location_city || "");
        closeModal(); toast(`Call ${r.call_number} created`);
        await refreshAll(); openCall(r.id);
      } catch (ex) { err.textContent = ex.message; }
    },
  },
    h("h2", {}, "New Call for Service"),
    h("div", { class: "grid" },
      field("Call type *", "call_type", "", { options: callTypeOptions(), required: true, cls: "wide" }),
      field("Priority (blank = default)", "priority", "", { options: S.ref.priorities.map((p) => [p, `P${p}`]) }),
      field("Source", "source", S.me.user.role === "officer" ? "OFFICER" : "PHONE", { options: S.ref.sources, blank: false }),
      field("Address / location", "location_address", "", { max: 200, cls: "wide" }),
      field("City / town", "location_city", lastCity, { max: 80 }),
      field("Cross street", "location_cross", "", { max: 200 }),
      field("Common place / business", "location_common", "", { max: 200, cls: "wide" }),
      field("Location notes", "location_notes", "", { max: 1000, cls: "wide" }),
      field("Caller name", "caller_name", "", { max: 150 }),
      field("Caller phone", "caller_phone", "", { max: 40, type: "tel" }),
      field("Caller address", "caller_address", "", { max: 200, cls: "wide" }),
      field("Initial narrative", "narrative", "", { textarea: true, rows: 4, max: 8000, cls: "full" })),
    avail.length && can("unit.status.any") ? h("div", {}, h("h3", {}, "Dispatch available units now"),
      h("div", { class: "row" }, avail.map((u) => h("label", { class: "check" }, h("input", { type: "checkbox", name: "u_" + u.id }), u.unit_code)))) : null,
    h("div", { style: "margin-top:10px" }, backEntry),
    h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "primary" }, "Create call"),
      h("button", { type: "button", onclick: closeModal }, "Cancel"), err));
  // Typing a type code: auto-set priority hint
  f.call_type.addEventListener("change", () => {
    const t = S.ref.call_types.find((x) => x.code === f.call_type.value);
    if (t && !f.priority.value) f.priority.querySelector("option").textContent = `Default (P${t.default_priority})`;
  });
  openModal(f);
}

// ---------------------------------------------------------------- call detail drawer
async function openCall(id) {
  S.callId = id;
  await loadDetail();
  if (S.view === "board") renderBoard();
}
function closeDrawer() {
  S.callId = null; S.detail = null;
  const d = $("#drawer"); if (d) d.remove();
  if (S.me && S.view === "board") renderBoard();
}
async function loadDetail(background = false) {
  const id = S.callId;
  if (!id) return;
  // Avoid clobbering a form the user is typing in during background refresh.
  if (background) {
    const dr = $("#drawer");
    if (dr && dr.contains(document.activeElement) && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  }
  try {
    const d = await api("GET", `/api/calls/${id}`, undefined, { background });
    if (S.callId !== id) return;
    S.detail = d; S.detailSeq = S.seq;
    renderDrawer();
  } catch (e) { if (e.message !== "Not signed in") toast(e.message, true); }
}

function renderDrawer() {
  const d = S.detail;
  const c = d.call;
  const old = $("#drawer");
  const scroll = old ? old.querySelector(".body").scrollTop : 0;
  const closed = c.status === "CLOSED";
  const editable = can("call.edit") && (!closed || can("call.reopen"));
  const head = h("div", { class: "head" },
    h("span", { class: "num" }, c.call_number),
    h("span", { class: `badge b-${c.status}` }, c.status),
    h("span", { class: `prio prio-${c.priority}` }, `P${c.priority}`),
    h("strong", {}, c.call_type),
    c.case_number ? h("span", {}, "Case ", h("strong", { class: "mono" }, c.case_number)) :
      can("narrative") ? h("button", { class: "small", onclick: () => act(() => api("POST", `/api/calls/${c.id}/case-number`), "Case number assigned") }, "Get case #") : null,
    h("span", { class: "grow" }),
    DEMO ? null : h("button", { class: "small", onclick: () => window.print() }, "Print"),
    h("button", { class: "small", onclick: closeDrawer, title: "Esc" }, "✕ Close"));

  // --- details form
  const err = h("div", { class: "error" });
  const df = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      const body = formData(df);
      body.version = c.version;
      if (!body.received_at) delete body.received_at;
      try { await api("PUT", `/api/calls/${c.id}`, body); toast("Call updated"); await refreshAll(); }
      catch (ex) { err.textContent = ex.message; }
    },
  },
    h("fieldset", { disabled: !editable, style: "border:0;padding:0;margin:0" },
      h("div", { class: "grid" },
        field("Call type", "call_type", c.call_type, { options: callTypeOptions(), blank: false, cls: "wide" }),
        field("Priority", "priority", c.priority, { options: S.ref.priorities.map((p) => [p, `P${p}`]), blank: false }),
        field("Source", "source", c.source, { options: S.ref.sources, blank: false }),
        field("Address / location", "location_address", c.location_address, { max: 200, cls: "wide" }),
        field("City / town", "location_city", c.location_city, { max: 80 }),
        field("Cross street", "location_cross", c.location_cross, { max: 200 }),
        field("Common place", "location_common", c.location_common, { max: 200, cls: "wide" }),
        field("Location notes", "location_notes", c.location_notes, { max: 1000, cls: "wide" }),
        field("Caller name", "caller_name", c.caller_name, { max: 150 }),
        field("Caller phone", "caller_phone", c.caller_phone, { max: 40 }),
        field("Caller address", "caller_address", c.caller_address, { max: 200, cls: "wide" }),
        can("time.edit") ? field("Received at", "received_at", c.received_at, { type: "datetime-local" }) : null,
        field("Summary", "summary", c.summary, { textarea: true, rows: 2, max: 4000, cls: "full" })),
      editable ? h("div", { class: "row no-print", style: "margin-top:8px" }, h("button", { class: "primary" }, "Save changes"), err) : null));

  const meta = h("dl", { class: "kv" },
    h("dt", {}, "Received"), h("dd", { class: "mono" }, fmt(c.received_at)),
    closed ? [h("dt", {}, "Closed"), h("dd", { class: "mono" }, `${fmt(c.closed_at)} — ${c.disposition}`)] : null);

  // --- units
  const assignable = S.board.units.filter((u) => u.status !== "OFF_DUTY" && u.current_call_id !== c.id);
  const unitSel = h("select", {}, h("option", { value: "" }, "Dispatch unit…"),
    assignable.map((u) => h("option", { value: u.id }, `${u.unit_code} — ${label(u.status)}${u.current_call_number ? " on " + u.current_call_number : ""}`)));
  const timeCell = (cu, k) => h("td", { class: "mono" }, fmt(cu[k], false));
  const unitsTbl = h("table", {},
    h("thead", {}, h("tr", {}, ["Unit", "Officer", "Disp", "Enr", "OnS", "Trn", "Clr", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, d.units.length ? d.units.map((cu) => h("tr", {},
      h("td", {}, h("strong", {}, cu.unit_code), cu.is_primary ? h("span", { class: "muted" }, " (pri)") : null),
      h("td", {}, cu.officer_name || ""),
      timeCell(cu, "dispatched_at"), timeCell(cu, "enroute_at"), timeCell(cu, "onscene_at"), timeCell(cu, "transport_at"), timeCell(cu, "cleared_at"),
      h("td", { class: "no-print" }, can("time.edit") ? h("button", { class: "small", onclick: () => unitTimesModal(cu) }, "times") : null))) :
      h("tr", {}, h("td", { colspan: 8, class: "muted" }, "No units assigned."))));
  const unitsBlock = h("div", {}, unitsTbl,
    can("unit.status.any") && !closed ? h("div", { class: "row no-print", style: "margin-top:6px" }, unitSel,
      h("button", { onclick: () => unitSel.value && act(() => api("POST", `/api/units/${unitSel.value}/status`, { status: "DISPATCHED", call_id: c.id }), "Unit dispatched") }, "Dispatch"),
      h("span", { class: "muted" }, "Status changes for assigned units: use the unit buttons on the board.")) : null,
    !closed ? h("div", { class: "row no-print", style: "margin-top:6px" },
      S.board.units.filter((u) => u.current_call_id === c.id && (can("unit.status.any") || u.officer_user_id === S.me.user.id)).map((u) =>
        h("div", { class: "row" }, h("strong", {}, u.unit_code), unitButtons(u)))) : null);

  // --- narrative
  const nText = h("textarea", { rows: 3, maxlength: 8000, placeholder: "Add narrative entry… (Ctrl+Enter to save)" });
  const addNote = async () => {
    if (!nText.value.trim()) return;
    const r = await act(() => api("POST", `/api/calls/${c.id}/narrative`, { text: nText.value }));
    if (r) nText.value = "";
  };
  nText.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addNote(); } });
  const narr = h("div", { class: "narr" }, d.narrative.map((n) => h("div", { class: `e ${n.entry_type}` },
    h("span", { class: "t" }, fmt(n.at)), h("span", { class: "muted" }, n.username || "system"), h("span", { class: "x" }, n.text))));
  const narrBlock = h("div", {}, narr, can("narrative") ? h("div", { class: "no-print", style: "margin-top:6px" }, nText,
    h("button", { class: "primary", style: "margin-top:4px", onclick: addNote }, "Add entry")) : null);

  // --- persons / vehicles
  const personsBlock = entityBlock(c, "persons", d.persons,
    (p) => [p.role, [p.last_name, p.first_name].filter(Boolean).join(", ") + (p.middle_name ? " " + p.middle_name : "") + (p.suffix ? " " + p.suffix : ""),
      p.dob || "", [p.sex, p.race].filter(Boolean).join("/"), [p.address, p.city, p.state].filter(Boolean).join(", "), p.phone || "",
      p.dl_number ? `${p.dl_number} ${p.dl_state || ""}` : ""],
    ["Role", "Name", "DOB", "Sex/Race", "Address", "Phone", "OLN"]);
  const vehiclesBlock = entityBlock(c, "vehicles", d.vehicles,
    (v) => [v.role, `${v.plate || ""} ${v.plate_state || ""}`, [v.year, v.color, v.make, v.model, v.style].filter(Boolean).join(" "), v.vin || "", v.notes || ""],
    ["Role", "Plate", "Vehicle", "VIN", "Notes"]);

  // --- close / reopen
  let closeBlock = null;
  if (!closed && can("call.close")) {
    const cf = h("form", {
      class: "row",
      onsubmit: async (e) => {
        e.preventDefault();
        const r = await act(() => api("POST", `/api/calls/${c.id}/close`, formData(cf)), `Call ${c.call_number} closed`);
        if (r) closeDrawer();
      },
    },
      h("select", { name: "disposition", required: true }, h("option", { value: "" }, "Disposition…"),
        S.ref.dispositions.filter((x) => x.active).map((x) => h("option", { value: x.code }, `${x.code} — ${x.description}`))),
      h("input", { name: "note", placeholder: "Closing note (optional)", maxlength: 4000, class: "grow", style: "width:auto" }),
      h("button", { class: "danger" }, "Close call"));
    closeBlock = h("div", { class: "sub no-print" }, h("h3", { style: "margin-top:0" }, "Close call"), cf,
      d.units.some((u) => !u.cleared_at) ? h("div", { class: "muted" }, "Assigned units will be cleared to AVAILABLE.") : null);
  } else if (closed && can("call.reopen")) {
    closeBlock = h("div", { class: "sub no-print" }, h("button", {
      onclick: () => {
        ask(`Reopen call ${c.call_number}`, { input: { label: "Reason for reopening" }, okLabel: "Reopen call" })
          .then((reason) => reason && act(() => api("POST", `/api/calls/${c.id}/reopen`, { reason }), "Call reopened"));
      },
    }, "Reopen call"));
  }

  const prior = d.prior_at_address.length ? h("div", {}, h("h3", {}, `Prior calls at this address (${d.prior_at_address.length})`),
    h("table", {}, h("tbody", {}, d.prior_at_address.map((p) => h("tr", { class: "click", onclick: () => openCall(p.id) },
      h("td", { class: "mono" }, p.call_number), h("td", { class: "mono" }, fmtShort(p.received_at)), h("td", {}, p.call_type), h("td", {}, p.disposition || "open")))))) : null;

  const body = h("div", { class: "body" },
    h("div", { class: "print-only" }, h("h2", {}, `${S.me.agency.name} — Call ${c.call_number}`), h("div", {}, `Printed ${fmt(new Date().toISOString())} by ${S.me.user.username}`)),
    meta,
    h("h3", {}, "Narrative / chronology"), narrBlock,
    h("h3", {}, "Units"), unitsBlock,
    h("h3", {}, "Call details"), df,
    h("h3", {}, "Persons"), personsBlock,
    h("h3", {}, "Vehicles"), vehiclesBlock,
    prior, closeBlock);
  const drawer = h("aside", { class: "drawer" + (S.view === "board" ? " beside" : ""), id: "drawer" }, head, body);
  const top = $("header.top");
  document.documentElement.style.setProperty("--drawer-top", top ? `${top.getBoundingClientRect().bottom}px` : "0px");
  if (old) old.replaceWith(drawer); else $("#app").append(drawer);
  body.scrollTop = scroll;
}

function entityBlock(c, kind, items, cells, headers) {
  const tbl = h("table", {},
    h("thead", {}, h("tr", {}, headers.map((t) => h("th", {}, t)), h("th", { class: "no-print" }))),
    h("tbody", {}, items.length ? items.map((it) => h("tr", {}, cells(it).map((v) => h("td", {}, v)),
      h("td", { class: "no-print" }, can("entity.edit") ? [
        h("button", { class: "small", onclick: () => entityModal(c, kind, it) }, "edit"), " ",
        h("button", { class: "small danger", onclick: () => ask("Remove this record from the call? It stays in the audit history.", { okLabel: "Remove", danger: true })
          .then((ok) => ok && act(() => api("DELETE", `/api/${kind}/${it.id}`), "Removed")) }, "remove")] : null))) :
      h("tr", {}, h("td", { colspan: headers.length + 1, class: "muted" }, "None."))));
  return h("div", {}, tbl, can("entity.edit") ? h("button", { class: "small no-print", style: "margin-top:6px", onclick: () => entityModal(c, kind, null) },
    kind === "persons" ? "+ Add person" : "+ Add vehicle") : null);
}

function entityModal(c, kind, it) {
  const v = it || {};
  const err = h("div", { class: "error" });
  const fields = kind === "persons" ? [
    field("Role *", "role", v.role, { options: S.ref.person_roles, required: true }),
    field("Last name", "last_name", v.last_name, { max: 80 }), field("First name", "first_name", v.first_name, { max: 80 }),
    field("Middle", "middle_name", v.middle_name, { max: 80 }), field("Suffix", "suffix", v.suffix, { max: 10 }),
    field("DOB", "dob", v.dob, { type: "date" }), field("Sex", "sex", v.sex, { options: ["M", "F", "X", "U"] }),
    field("Race", "race", v.race, { max: 20 }), field("Height", "height", v.height, { max: 10 }), field("Weight", "weight", v.weight, { max: 10 }),
    field("Address", "address", v.address, { max: 200, cls: "wide" }), field("City", "city", v.city, { max: 80 }),
    field("State", "state", v.state, { max: 2 }), field("ZIP", "zip", v.zip, { max: 10 }), field("Phone", "phone", v.phone, { max: 40 }),
    field("Operator license #", "dl_number", v.dl_number, { max: 40 }), field("OLN state", "dl_state", v.dl_state, { max: 2 }),
    field("Notes", "notes", v.notes, { textarea: true, max: 2000, cls: "full" }),
  ] : [
    field("Role *", "role", v.role, { options: S.ref.vehicle_roles, required: true }),
    field("Plate", "plate", v.plate, { max: 15 }), field("Plate state", "plate_state", v.plate_state, { max: 2 }),
    field("Year", "year", v.year, { max: 4, inputmode: "numeric" }), field("Make", "make", v.make, { max: 30 }), field("Model", "model", v.model, { max: 40 }),
    field("Style", "style", v.style, { max: 20 }), field("Color", "color", v.color, { max: 30 }), field("VIN", "vin", v.vin, { max: 17, cls: "wide" }),
    field("Notes", "notes", v.notes, { textarea: true, max: 2000, cls: "full" }),
  ];
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      try {
        if (it) await api("PUT", `/api/${kind}/${it.id}`, formData(f));
        else await api("POST", `/api/calls/${c.id}/${kind}`, formData(f));
        closeModal(); toast("Saved"); await refreshAll();
      } catch (ex) { err.textContent = ex.message; }
    },
  }, h("h2", {}, `${it ? "Edit" : "Add"} ${kind === "persons" ? "person" : "vehicle"} — ${c.call_number}`),
    h("div", { class: "grid" }, fields),
    h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "primary" }, "Save"), h("button", { type: "button", onclick: closeModal }, "Cancel"), err));
  openModal(f);
}

function unitTimesModal(cu) {
  const err = h("div", { class: "error" });
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      try { await api("PUT", `/api/call-units/${cu.id}`, formData(f)); closeModal(); toast("Times updated"); await refreshAll(); }
      catch (ex) { err.textContent = ex.message; }
    },
  }, h("h2", {}, `Correct times — ${cu.unit_code}`),
    h("p", { class: "muted" }, "Use to enter times from paper logs or fix errors. Every change is recorded in the narrative and audit log."),
    h("div", { class: "grid" }, [["Dispatched", "dispatched_at"], ["En route", "enroute_at"], ["On scene", "onscene_at"], ["Transport", "transport_at"], ["Cleared", "cleared_at"]]
      .map(([l, k]) => field(l, k, cu[k], { type: "datetime-local" }))),
    h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "primary" }, "Save"), h("button", { type: "button", onclick: closeModal }, "Cancel"), err));
  openModal(f);
}

// ---------------------------------------------------------------- search
function searchView() {
  const results = h("div", {});
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      const d = formData(f);
      const qs = new URLSearchParams(Object.entries(d).filter(([, v]) => v)).toString();
      try {
        const r = await api("GET", "/api/calls/search?" + qs);
        results.replaceChildren(h("div", { class: "muted", style: "margin:8px 0" }, `${r.results.length} result(s)${r.results.length >= r.limit ? " — limit reached, narrow your search" : ""}`),
          h("table", {}, h("thead", {}, h("tr", {}, ["Call #", "Case #", "Received", "Type", "P", "Location", "Caller", "Units", "Status/Disp"].map((t) => h("th", {}, t)))),
            h("tbody", {}, r.results.map((c) => h("tr", { class: "click", onclick: () => openCall(c.id) },
              h("td", { class: "mono" }, c.call_number), h("td", { class: "mono" }, c.case_number || ""), h("td", { class: "mono" }, fmt(c.received_at)),
              h("td", {}, c.call_type), h("td", {}, c.priority), h("td", {}, [c.location_address, c.location_city].filter(Boolean).join(", ")),
              h("td", {}, c.caller_name || ""), h("td", { class: "mono" }, c.units || ""), h("td", {}, c.status === "CLOSED" ? c.disposition : c.status))))));
      } catch (ex) { toast(ex.message, true); }
    },
  },
    h("div", { class: "grid" },
      field("Any text (call #, case #, address, narrative…)", "q", "", { cls: "wide" }),
      field("Address / place", "address", ""), field("Person name", "name", ""), field("Plate", "plate", ""), field("Unit", "unit", ""),
      field("Call type", "call_type", "", { options: S.ref.call_types.map((t) => [t.code, `${t.code} — ${t.description}`]) }),
      field("Status", "status", "", { options: ["PENDING", "ACTIVE", "CLOSED"] }),
      field("Received from", "from", "", { type: "datetime-local" }), field("Received to", "to", "", { type: "datetime-local" })),
    h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "primary" }, "Search"), h("button", { type: "reset" }, "Clear")));
  return h("div", { class: "view" }, h("h2", {}, "Search calls"), f, results);
}

// ---------------------------------------------------------------- admin
function adminView() {
  const root = h("div", { class: "view" });
  const tabs = [];
  if (can("admin")) tabs.push(["users", "Users"]);
  if (can("unit.manage")) tabs.push(["units", "Units"]);
  if (can("admin")) tabs.push(["call_types", "Call types"], ["dispositions", "Dispositions"]);
  S.adminTab = S.adminTab && tabs.some(([t]) => t === S.adminTab) ? S.adminTab : tabs[0][0];
  const content = h("div", {});
  const tabBar = h("div", { class: "row", style: "margin-bottom:12px" }, tabs.map(([t, l]) =>
    h("button", { class: S.adminTab === t ? "primary" : "", onclick: () => { S.adminTab = t; root.replaceWith(adminView()); } }, l)));
  root.append(tabBar, content);
  const load = { users: adminUsers, units: adminUnits, call_types: () => adminRef("call-types", true), dispositions: () => adminRef("dispositions", false) }[S.adminTab];
  load().then((el) => content.replaceChildren(el)).catch((e) => toast(e.message, true));
  return root;
}
function rerenderAdmin() { const v = $("#main .view"); if (v) v.replaceWith(adminView()); }

async function adminUsers() {
  const { users } = await api("GET", "/api/admin/users");
  const err = h("div", { class: "error" });
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      try { await api("POST", "/api/admin/users", formData(f)); toast("User created. Give them the temporary password; they must change it at first sign-in."); rerenderAdmin(); }
      catch (ex) { err.textContent = ex.message; }
    },
  }, h("h3", {}, "Add user"), h("div", { class: "grid" },
    field("Username *", "username", "", { required: true, max: 64 }), field("Full name *", "full_name", "", { required: true, max: 100 }),
    field("Badge #", "badge", "", { max: 20 }), field("Role *", "role", "dispatcher", { options: S.ref.roles, blank: false }),
    h("label", {}, "Temporary password *", h("input", { name: "password", type: "text", required: true, autocomplete: "off" }))),
    h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "primary" }, "Create user"), err));
  const doAct = (u, body, msg) => act(() => api("PUT", `/api/admin/users/${u.id}`, body), msg).then(rerenderAdmin);
  const tbl = h("table", {}, h("thead", {}, h("tr", {}, ["Username", "Name", "Badge", "Role", "MFA", "Status", "Last login", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, users.map((u) => {
      const roleSel = h("select", { onchange: () => doAct(u, { action: "update", role: roleSel.value }, "Role updated") },
        S.ref.roles.map((r) => h("option", { value: r, selected: r === u.role }, r)));
      return h("tr", {},
        h("td", { class: "mono" }, u.username), h("td", {}, u.full_name), h("td", {}, u.badge || ""), h("td", {}, roleSel),
        h("td", {}, u.totp_enabled ? "on" : h("span", { class: "muted" }, "off")),
        h("td", {}, !u.active ? h("span", { class: "error" }, "disabled") : u.locked ? h("span", { class: "error" }, "LOCKED") : u.must_change_pw ? "temp pw" : "active"),
        h("td", { class: "mono" }, fmtShort(u.last_login_at)),
        h("td", { class: "row" },
          h("button", { class: "small", onclick: () => ask(`Reset password for ${u.username}`, { input: { label: "New temporary password" }, okLabel: "Reset password" })
            .then((pw) => pw && doAct(u, { action: "reset_password", password: pw }, "Password reset")) }, "reset pw"),
          u.locked ? h("button", { class: "small", onclick: () => doAct(u, { action: "unlock" }, "Unlocked") }, "unlock") : null,
          u.totp_enabled ? h("button", { class: "small", onclick: () => ask(`Reset two-factor for ${u.username}? They will re-enroll at next sign-in.`, { okLabel: "Reset", danger: true })
            .then((ok) => ok && doAct(u, { action: "reset_mfa" }, "MFA reset")) }, "reset MFA") : null,
          h("button", { class: "small" + (u.active ? " danger" : ""), onclick: () => doAct(u, { action: "update", active: !u.active }, u.active ? "Disabled" : "Enabled") }, u.active ? "disable" : "enable")));
    })));
  return h("div", {}, tbl, f);
}

async function adminUnits() {
  const { units } = await api("GET", "/api/admin/units");
  const err = h("div", { class: "error" });
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      try { await api("POST", "/api/admin/units", formData(f)); toast("Unit added"); S.seq = null; await refreshBoard(); rerenderAdmin(); }
      catch (ex) { err.textContent = ex.message; }
    },
  }, h("h3", {}, "Add unit"), h("div", { class: "grid" },
    field("Unit code * (e.g. 1A21, CAR3)", "unit_code", "", { required: true, max: 15 }), field("Description", "description", "", { max: 100 }),
    field("Type", "unit_type", "PATROL", { options: S.ref.unit_types, blank: false })),
    h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "primary" }, "Add unit"), err));
  const tbl = h("table", {}, h("thead", {}, h("tr", {}, ["Code", "Description", "Type", "Status", "Active", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, units.map((u) => h("tr", {},
      h("td", { class: "mono" }, u.unit_code), h("td", {}, u.description || ""), h("td", {}, u.unit_type), h("td", {}, label(u.status)),
      h("td", {}, u.active ? "yes" : "no"),
      h("td", {}, h("button", { class: "small", onclick: () => act(() => api("PUT", `/api/admin/units/${u.id}`, { active: !u.active }), "Updated").then(rerenderAdmin) }, u.active ? "deactivate" : "activate"),
        " ", h("button", { class: "small", onclick: () => unitHistoryModal(u) }, "history"))))));
  return h("div", {}, tbl, f);
}

async function unitHistoryModal(u) {
  const { history } = await api("GET", `/api/units/${u.id}/history`);
  openModal(h("div", {}, h("h2", {}, `Status history — ${u.unit_code}`),
    h("table", {}, h("tbody", {}, history.map((x) => h("tr", {}, h("td", { class: "mono" }, fmt(x.at)), h("td", {}, label(x.status)), h("td", { class: "mono" }, x.call_number || ""), h("td", {}, x.note || ""))))),
    h("button", { style: "margin-top:10px", onclick: closeModal }, "Close")));
}

async function adminRef(path, withPrio) {
  const { items } = await api("GET", `/api/admin/${path}`);
  const save = async (body) => { await act(() => api("POST", `/api/admin/${path}`, body), "Saved"); S.ref = await api("GET", "/api/reference"); rerenderAdmin(); };
  const err = h("div", { class: "error" });
  const f = h("form", { onsubmit: (e) => { e.preventDefault(); save(formData(f)); } },
    h("h3", {}, "Add / update (same code overwrites)"),
    h("div", { class: "grid" }, field("Code *", "code", "", { required: true, max: 20 }), field("Description *", "description", "", { required: true, max: 100, cls: "wide" }),
      withPrio ? field("Default priority", "default_priority", 3, { options: S.ref.priorities, blank: false }) : null),
    h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "primary" }, "Save"), err));
  const tbl = h("table", {}, h("thead", {}, h("tr", {}, ["Code", "Description", withPrio ? "Default P" : null, "Active", ""].filter(Boolean).map((t) => h("th", {}, t)))),
    h("tbody", {}, items.map((x) => h("tr", {}, h("td", { class: "mono" }, x.code), h("td", {}, x.description), withPrio ? h("td", {}, x.default_priority) : null,
      h("td", {}, x.active ? "yes" : "no"),
      h("td", {}, h("button", { class: "small", onclick: () => save({ ...x, active: !x.active }) }, x.active ? "deactivate" : "activate"))))));
  return h("div", {}, tbl, f);
}

// ---------------------------------------------------------------- audit
function auditView() {
  const out = h("div", {});
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      const qs = new URLSearchParams(Object.entries(formData(f)).filter(([, v]) => v)).toString();
      try {
        const r = await api("GET", "/api/audit?" + qs);
        out.replaceChildren(
          h("p", { class: r.chain_ok ? "" : "error" }, r.chain_ok ? `✔ Audit chain verified (${r.chain_rows} entries, no tampering detected).` :
            `✖ AUDIT CHAIN BROKEN at entry ${r.chain_first_bad_id}. Notify your administrator / CJIS TAC.`),
          h("table", {}, h("thead", {}, h("tr", {}, ["#", "Time", "User", "IP", "Action", "Entity", "Detail"].map((t) => h("th", {}, t)))),
            h("tbody", {}, r.entries.map((x) => h("tr", {}, h("td", { class: "mono" }, x.id), h("td", { class: "mono" }, fmt(x.at)), h("td", {}, x.username || ""),
              h("td", { class: "mono" }, x.ip || ""), h("td", {}, x.action), h("td", {}, x.entity ? `${x.entity} ${x.entity_id || ""}` : ""),
              h("td", { class: "mono", style: "font-size:11px;word-break:break-all" }, x.detail || ""))))));
      } catch (ex) { toast(ex.message, true); }
    },
  }, h("div", { class: "grid" }, field("Username", "username", ""), field("Action", "action", "", { placeholder: "e.g. LOGIN_FAIL, CALL_VIEW" }),
    field("Entity", "entity", "", { options: ["call", "unit", "user", "person", "vehicle", "export"] }), field("Entity id", "entity_id", ""),
    field("Max rows", "limit", "200")),
  h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "primary" }, "Load audit log")));
  setTimeout(() => f.requestSubmit(), 0);
  return h("div", { class: "view" }, h("h2", {}, "Audit log"), f, out);
}

// ---------------------------------------------------------------- export
function exportView() {
  const f = h("form", {
    onsubmit: (e) => {
      e.preventDefault();
      const d = formData(f);
      const qs = new URLSearchParams(Object.entries(d).filter(([, v]) => v)).toString();
      if (DEMO) { toast("Downloads are turned off in the demo. In the real system this saves a ZIP of every record."); return; }
      window.location.href = "/api/export" + (qs ? "?" + qs : "");
      toast("Export started — check your downloads.");
    },
  }, h("div", { class: "grid" }, field("Calls received from (optional)", "from", "", { type: "datetime-local" }),
    field("Calls received to (optional)", "to", "", { type: "datetime-local" })),
  h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "primary" }, "Download export (.zip)")));
  const arch = h("div", { class: "sub" }, h("span", { class: "muted" }, "Loading archive status…"));
  api("GET", "/api/archive/status").then((st) => {
    if (!st.enabled) {
      arch.replaceChildren(h("strong", {}, "Call archive folder: not configured."),
        h("div", { class: "muted" }, "Set [archive] dir in config.ini (for example I:\\CAD\\Calls) to keep a copy of every call on a network drive."));
      return;
    }
    arch.replaceChildren(
      h("div", {}, h("strong", {}, "Call archive folder: "), h("span", { class: "mono" }, st.dir)),
      h("div", { class: st.last_error ? "error" : "" }, st.last_error ? `Problem: ${st.last_error} — will retry automatically.` :
        `Last successful copy: ${st.last_ok ? fmt(st.last_ok) : "not yet"}.`),
      h("div", {}, `${st.calls_total} calls total · ${st.calls_not_yet_archived} waiting to be copied · ${st.written_total} files written since server start`),
      h("button", { class: "small", style: "margin-top:6px", onclick: () => act(() => api("POST", "/api/archive/run"), "Archive copy started").then(() => setTimeout(() => setView("export"), 1500)) }, "Copy now"));
  }).catch((e) => arch.replaceChildren(h("span", { class: "error" }, e.message)));
  return h("div", { class: "view" }, h("h2", {}, "Export data"), arch,
    h("p", {}, "Downloads every call, narrative entry, unit time, person, vehicle, unit status change, and the audit log as JSON and CSV — " +
      "for import into Valcour when it returns or into a replacement system. Leave the dates blank to export everything."),
    h("p", { class: "muted" }, "The export contains criminal justice information. Store it only on agency-controlled, encrypted media. The export itself is recorded in the audit log."),
    f);
}

// ---------------------------------------------------------------- account
function accountModal() {
  const err = h("div", { class: "error" });
  const f = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      const d = formData(f);
      if (d.new_password !== d.confirm) { err.textContent = "Passwords do not match."; return; }
      try { await api("POST", "/api/me/password", { current_password: d.current_password, new_password: d.new_password }); toast("Password changed"); closeModal(); }
      catch (ex) { err.textContent = ex.message; }
    },
  }, h("h3", {}, "Change password"), h("div", { class: "grid" },
    h("label", {}, "Current", h("input", { type: "password", name: "current_password", required: true, autocomplete: "current-password" })),
    h("label", {}, "New", h("input", { type: "password", name: "new_password", required: true, autocomplete: "new-password" })),
    h("label", {}, "Confirm", h("input", { type: "password", name: "confirm", required: true, autocomplete: "new-password" }))),
  h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "primary" }, "Change password"), err));
  openModal(h("div", {}, h("h2", {}, `Account — ${S.me.user.username}`),
    h("p", { class: "muted" }, `Role: ${S.me.user.role}. Two-factor: ${S.me.user.totp_enabled ? "enabled" : "not enabled"}. Version ${S.me.version}.`),
    f, !S.me.user.totp_enabled ? h("div", {}, h("h3", {}, "Two-factor sign-in"), mfaSetupBlock(async () => { closeModal(); await boot(); })) : null,
    h("div", { style: "margin-top:12px" }, h("button", { onclick: closeModal }, "Close"))));
}

// ---------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  if (!S.me) return;
  if (e.key === "F2") { e.preventDefault(); newCallModal(); }
  else if (e.key === "Escape") { if ($("#modal")) closeModal(); else if (S.callId) closeDrawer(); }
});
window.addEventListener("beforeunload", (e) => {
  const t = document.querySelector("#drawer textarea");
  if (t && t.value.trim()) { e.preventDefault(); e.returnValue = ""; }
});

boot();
