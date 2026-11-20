// Injected by tracker/desktop.py into every page of the Wyze sign-in window.
// The address bar shows everywhere; credential/key capture only runs on *.wyze.com.
(function () {
  if (window.__wstHooked) return;
  window.__wstHooked = true;

  const host = location.hostname;
  const isWyze = /(^|\.)wyze\.com$/.test(host);
  const BAR_H = 40;
  const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
  const TOKEN = /\b[A-Za-z0-9]{40,100}\b/g;

  const ready = new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) resolve(window.pywebview.api);
    else window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });
  });
  const call = (fn, ...args) => ready.then((api) => (api[fn] ? api[fn](...args) : null));
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // ---- address bar ---------------------------------------------------------
  const LOCK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" ' +
    'stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>';
  const WARN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 21h20L12 3z"/><path d="M12 10v5M12 18h.01"/></svg>';
  const SSO = { "accounts.google.com": "Google sign-in", "appleid.apple.com": "Apple sign-in" };
  let cert = null;

  function verdict() {
    if (location.protocol !== "https:") return { tone: "bad", icon: WARN, text: "Not secure — don't enter your password" };
    if (cert && !cert.ok) return { tone: "bad", icon: WARN, text: "Certificate check failed — " + esc(cert.error || "unknown") };
    if (isWyze) return { tone: "good", icon: LOCK, text: "Verified Wyze site" };
    if (SSO[host]) return { tone: "neutral", icon: LOCK, text: SSO[host] };
    return { tone: "warn", icon: WARN, text: "Not a Wyze address — don't enter your Wyze password here" };
  }

  function renderBar() {
    if (!document.body) return;
    let bar = document.getElementById("wst-addr");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "wst-addr";
      bar.style.cssText =
        `position:fixed;top:0;left:0;right:0;height:${BAR_H}px;z-index:2147483647;display:flex;align-items:center;` +
        "gap:10px;padding:0 10px;box-sizing:border-box;background:#f1f3f4;border-bottom:1px solid #d3d7db;" +
        "font:13px/1 'Segoe UI',system-ui,sans-serif;color:#202124;user-select:text";
      document.documentElement.appendChild(bar);
      document.documentElement.style.setProperty("scroll-padding-top", BAR_H + "px");
      document.body.style.setProperty("margin-top", BAR_H + "px", "important");
    }
    const v = verdict();
    const colors = {
      good: ["#137333", "#e6f4ea"], neutral: ["#3c4043", "#e8eaed"], warn: ["#b06000", "#fef7e0"], bad: ["#c5221f", "#fce8e6"],
    }[v.tone];
    const path = (location.pathname + location.search + location.hash).replace(/^\/$/, "");
    const certLine = cert && cert.ok
      ? `Connection is secure. Certificate for ${cert.subject} issued by ${cert.issuer}, valid until ${cert.expires}. ` +
        "Checked independently by Wyze Scale Tracker."
      : "Checking certificate…";
    const html =
      `<span title="${esc(certLine)}" style="display:flex;align-items:center;gap:6px;padding:5px 9px;border-radius:999px;` +
      `background:${colors[1]};color:${colors[0]};font-weight:600;white-space:nowrap;cursor:help">${v.icon}${v.text}</span>` +
      `<span title="${esc(location.href)}" style="flex:1;min-width:0;display:flex;align-items:center;height:28px;padding:0 12px;` +
      "border-radius:999px;background:#fff;border:1px solid #dadce0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis\">" +
      `<span style="color:#5f6368">${esc(location.protocol)}//</span><b style="color:#202124">${esc(host)}</b>` +
      `<span style="color:#5f6368;overflow:hidden;text-overflow:ellipsis">${esc(path)}</span></span>`;
    if (bar.__html !== html) { bar.innerHTML = html; bar.__html = html; }
    offsetFixedHeaders();
  }

  // Push the page's own fixed/sticky headers below the bar.
  function offsetFixedHeaders() {
    for (const el of document.body.querySelectorAll("*")) {
      if (el.id === "wst-banner" || el.id === "wst-countdown" || el.dataset.wstShifted) continue;
      const pos = getComputedStyle(el).position;
      if (pos !== "fixed" && pos !== "sticky") continue;
      const rect = el.getBoundingClientRect();
      if (rect.top >= BAR_H || rect.height > innerHeight / 2) continue;
      const top = parseFloat(getComputedStyle(el).top) || 0;
      el.style.setProperty("top", top + BAR_H + "px", "important");
      el.dataset.wstShifted = "1";
    }
  }

  call("get_certificate", host).then((info) => { cert = info; renderBar(); });
  renderBar();
  setInterval(renderBar, 800);
  window.addEventListener("hashchange", renderBar);

  if (!isWyze) return;

  // ---- banner ----------------------------------------------------------------
  let hasMaskedKey = false;
  function banner() {
    if (!document.body) return;
    const onConsole = host === "developer-api-console.wyze.com";
    let div = document.getElementById("wst-banner");
    if (!div) {
      div = document.createElement("div");
      div.id = "wst-banner";
      div.style.cssText =
        "position:fixed;left:12px;right:12px;bottom:12px;z-index:2147483647;padding:12px 16px;" +
        "border-radius:12px;background:#0f9d8a;color:#fff;font:600 14px/1.45 system-ui,sans-serif;" +
        "box-shadow:0 10px 30px rgba(0,0,0,.35)";
      document.body.appendChild(div);
    }
    const html = !onConsole
      ? "<b>Wyze Scale Tracker:</b> sign in with your Wyze email and password. If Wyze emails you a code, enter it here. " +
        "Your email, password and API key are saved only to this computer's <code>.env</code>."
      : hasMaskedKey
        ? "<b>Wyze Scale Tracker:</b> you already have an API key, and Wyze only shows its full value once. " +
          "Click <b>Delete</b>, then <b>Create API Key</b> — this window closes by itself once the new key appears."
        : "<b>Wyze Scale Tracker:</b> sign in, then click <b>Create API Key</b>. This window closes by itself once the key appears.";
    if (div.__html !== html) { div.innerHTML = html; div.__html = html; }
  }

  // ---- credentials on the sign-in page -------------------------------------
  let email = "", password = "";
  const isEmailField = (el) =>
    el.type === "email" || /e-?mail|user|login/i.test((el.name || "") + (el.id || "") + (el.autocomplete || "")) ||
    /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(el.value || "");
  function readFields() {
    for (const el of document.querySelectorAll("input")) {
      if (el.type === "password" && el.value) password = el.value;
      else if (el.type !== "hidden" && el.value && isEmailField(el)) email = el.value.trim();
    }
  }
  function flushCreds() {
    readFields();
    if (email || password) call("report_credentials", email, password);
  }
  document.addEventListener("change", flushCreds, true);
  document.addEventListener("submit", flushCreds, true);
  document.addEventListener("click", (e) => { if (e.target.closest("button,[type=submit],[role=button]")) flushCreds(); }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Enter") flushCreds(); }, true);

  // ---- API key on the developer console ------------------------------------
  // Wyze returns {keyId, apiKey, expirationTime}; getApiKey masks apiKey for existing keys,
  // createApiKey returns the full 60-char value once.
  let lastSent = "";
  function report(keyId, apiKey, expires, source) {
    const sig = keyId + ":" + apiKey;
    if (!keyId || !apiKey || sig === lastSent) return;
    lastSent = sig;
    call("report_api_key", keyId, apiKey, source, expires || null);
  }
  function fromJson(obj) {
    let keyId = null, apiKey = null, expires = null, masked = false;
    (function walk(o) {
      if (!o || typeof o !== "object") return;
      for (const [k, v] of Object.entries(o)) {
        if (typeof v === "string") {
          if (!keyId && UUID.test(v) && v.length === 36 && /key.?id|^id$/i.test(k)) keyId = v;
          else if (/api.?key|^key$|secret/i.test(k)) {
            if (/^[A-Za-z0-9]{40,100}$/.test(v)) apiKey = apiKey || v;
            else if (v) masked = true;
          }
        } else if (typeof v === "number" && /expir/i.test(k)) expires = v;
        else walk(v);
      }
    })(obj);
    return { keyId, apiKey, expires, masked };
  }
  function inspect(url, text) {
    if (!/account\/(get|create)ApiKey/i.test(url || "")) return;
    try {
      const data = JSON.parse(text);
      call("debug_shape", url, JSON.stringify(data, (k, v) => (typeof v === "string" ? `<str ${v.length}>` : v)));
      const found = fromJson(data);
      if (found.apiKey) report(found.keyId, found.apiKey, found.expires, "network");
      else if (found.keyId && found.masked) { hasMaskedKey = true; banner(); }
    } catch (_) {}
  }
  const origFetch = window.fetch;
  window.fetch = async function (...args) {
    const res = await origFetch.apply(this, args);
    try {
      const url = typeof args[0] === "string" ? args[0] : args[0].url;
      if (/ApiKey/i.test(url)) res.clone().text().then((t) => inspect(url, t));
    } catch (_) {}
    return res;
  };
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.addEventListener("load", () => { try { inspect(String(url), this.responseText); } catch (_) {} });
    return origOpen.call(this, method, url, ...rest);
  };
  function scanDom() {
    if (host !== "developer-api-console.wyze.com" || !document.body) return;
    const text = document.body.innerText + " " +
      [...document.querySelectorAll("input,textarea")].map((el) => el.value).join(" ");
    const id = (text.match(UUID) || [])[0];
    const tokens = (text.match(TOKEN) || []).filter((t) => !/^[0-9a-f]{32}$/i.test(t));
    if (id && tokens.length) report(id, tokens[0], null, "page");
  }

  // ---- countdown overlay (called from Python) ------------------------------
  window.__wstCountdown = function (seconds, message) {
    let el = document.getElementById("wst-countdown");
    if (!el) {
      el = document.createElement("div");
      el.id = "wst-countdown";
      el.style.cssText =
        `position:fixed;inset:${BAR_H}px 0 0 0;z-index:2147483646;display:grid;place-items:center;background:rgba(10,20,25,.82);` +
        "color:#fff;font:700 26px/1.4 system-ui,sans-serif;text-align:center";
      document.body.appendChild(el);
    }
    let n = seconds;
    const tick = () => {
      el.innerHTML = `<div>✅ ${esc(message)}<br><span style="font-size:18px;opacity:.85">Restarting in ${n}…</span></div>`;
      if (n-- > 0) setTimeout(tick, 1000);
    };
    tick();
  };

  banner();
  setInterval(() => { banner(); readFields(); scanDom(); }, 1000);
})();
