"""
Shared controls for static HTML reports (file:// safe, no CDN).

**Time:** elements with ``data-utc="YYYY-MM-DDTHH:MM:SSZ"``; UTC / Local buttons.
  Preference: cookie ``smsRipperTzDisplay`` (``utc`` | ``local``).

**Theme:** Dark (default) / Light; ``html[data-theme="light"]`` toggles CSS variables.
  Preference: ``localStorage`` key ``smsRipperTheme`` (``dark`` | ``light``).

Some browsers limit cookies on ``file://``; localStorage for theme is usually reliable.
"""

from __future__ import annotations

# Page + component colors (dark defaults; light overrides on html[data-theme="light"]).
THEME_CSS = """
:root {
  --sr-bg-page: #111;
  --sr-fg: #e8e8e8;
  --sr-fg-muted: #888;
  --sr-border: #333;
  --sr-th-bg: #1e1e1e;
  --sr-tr-alt: #161616;
  --sr-link: #8cb4ff;
  --sr-link-hover: #bcd4ff;
  --sr-link-visited: #c4a7e7;
  --sr-hint-bg: #1a1a2e;
  --sr-hint-border: #334;
  --sr-err: #f66;
  --sr-pre-bg: #0d0d0d;
  --sr-badge-ok: #3d8b3d;
  --sr-badge-err: #f66;
  --sr-badge-inc: #fa0;
  --sr-bar-bg: #1e1e1e;
  --sr-bar-border: #444;
  --sr-bar-shadow: rgba(0, 0, 0, 0.45);
  --sr-bar-label: #888;
  --sr-bar-btn-bg: #2a2a2a;
  --sr-bar-btn-border: #555;
  --sr-bar-btn-fg: #bbb;
  --sr-bar-btn-hover-fg: #e8e8e8;
  --sr-bar-btn-hover-border: #666;
  --sr-bar-btn-active-bg: #2a3550;
  --sr-bar-btn-active-border: #8cb4ff;
  --sr-bar-btn-active-fg: #e8e8e8;
}
html[data-theme="light"] {
  --sr-bg-page: #f2f3f5;
  --sr-fg: #1a1d24;
  --sr-fg-muted: #5c6570;
  --sr-border: #c9cdd3;
  --sr-th-bg: #e4e6ea;
  --sr-tr-alt: #eceef2;
  --sr-link: #0b57d0;
  --sr-link-hover: #0842a0;
  --sr-link-visited: #6b4fa0;
  --sr-hint-bg: #e8edf7;
  --sr-hint-border: #b8c5dc;
  --sr-err: #c62828;
  --sr-pre-bg: #fff;
  --sr-badge-ok: #1b5e20;
  --sr-badge-err: #c62828;
  --sr-badge-inc: #e65100;
  --sr-bar-bg: #fff;
  --sr-bar-border: #c9cdd3;
  --sr-bar-shadow: rgba(0, 0, 0, 0.12);
  --sr-bar-label: #5c6570;
  --sr-bar-btn-bg: #f0f1f4;
  --sr-bar-btn-border: #c9cdd3;
  --sr-bar-btn-fg: #3a424d;
  --sr-bar-btn-hover-fg: #1a1d24;
  --sr-bar-btn-hover-border: #9aa3ad;
  --sr-bar-btn-active-bg: #d6e3fc;
  --sr-bar-btn-active-border: #0b57d0;
  --sr-bar-btn-active-fg: #062e6b;
}
span.dt-adjustable { font-weight: 500; line-height: 1.4; }
th.col-datetime, td.col-datetime {
  min-width: 11rem;
  max-width: 14rem;
  white-space: normal;
}
"""

# Fixed top-right: theme row + time row.
TOGGLE_CSS = """
#sms-ripper-top-bar {
  position: fixed;
  top: 0.65rem;
  right: 0.65rem;
  z-index: 10000;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.4rem;
}
#sms-ripper-theme-bar,
#sms-ripper-tz-bar {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.55rem;
  font-size: 0.75rem;
  background: var(--sr-bar-bg);
  border: 1px solid var(--sr-bar-border);
  border-radius: 6px;
  box-shadow: 0 2px 8px var(--sr-bar-shadow);
}
#sms-ripper-theme-bar .tz-bar-label,
#sms-ripper-tz-bar .tz-bar-label {
  color: var(--sr-bar-label);
  margin-right: 0.1rem;
}
#sms-ripper-theme-bar button.theme-toggle-btn,
#sms-ripper-tz-bar button.tz-toggle-btn {
  margin: 0;
  padding: 0.2rem 0.55rem;
  font: inherit;
  font-size: 0.75rem;
  cursor: pointer;
  border-radius: 4px;
  border: 1px solid var(--sr-bar-btn-border);
  background: var(--sr-bar-btn-bg);
  color: var(--sr-bar-btn-fg);
}
#sms-ripper-theme-bar button.theme-toggle-btn:hover,
#sms-ripper-tz-bar button.tz-toggle-btn:hover {
  color: var(--sr-bar-btn-hover-fg);
  border-color: var(--sr-bar-btn-hover-border);
}
#sms-ripper-theme-bar button.theme-toggle-btn.active,
#sms-ripper-tz-bar button.tz-toggle-btn.active {
  background: var(--sr-bar-btn-active-bg);
  border-color: var(--sr-bar-btn-active-border);
  color: var(--sr-bar-btn-active-fg);
}
"""

TOGGLE_HTML = """
<div id="sms-ripper-top-bar">
  <div id="sms-ripper-theme-bar" role="group" aria-label="Color theme">
    <span class="tz-bar-label">Theme</span>
    <button type="button" class="theme-toggle-btn active" data-theme-choice="dark" aria-pressed="true">Dark</button>
    <button type="button" class="theme-toggle-btn" data-theme-choice="light" aria-pressed="false">Light</button>
  </div>
  <div id="sms-ripper-tz-bar" role="group" aria-label="Time zone display">
    <span class="tz-bar-label">Time</span>
    <button type="button" class="tz-toggle-btn active" data-tz="utc" aria-pressed="true">UTC</button>
    <button type="button" class="tz-toggle-btn" data-tz="local" aria-pressed="false">Local</button>
  </div>
</div>
"""

# Run in <head> after THEME_CSS so the first paint matches saved theme (avoids flash).
THEME_BOOTSTRAP_HEAD = """
<script>
(function () {
  try {
    var v = localStorage.getItem("smsRipperTheme");
    if (v === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
  } catch (e) {}
})();
</script>
"""

THEME_JS = """
<script>
(function () {
  var KEY = "smsRipperTheme";
  function getStored() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null;
    }
  }
  function setStored(v) {
    try {
      localStorage.setItem(KEY, v);
    } catch (e) {}
  }
  function applyTheme(choice) {
    var theme = choice === "light" ? "light" : "dark";
    if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
    var bar = document.getElementById("sms-ripper-theme-bar");
    if (!bar) return;
    var btns = bar.querySelectorAll("button[data-theme-choice]");
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].getAttribute("data-theme-choice") === theme;
      btns[i].classList.toggle("active", on);
      btns[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }
  function initTheme() {
    var bar = document.getElementById("sms-ripper-theme-bar");
    if (!bar) return;
    var saved = getStored();
    if (saved !== "light" && saved !== "dark") saved = "dark";
    applyTheme(saved);
    var btns = bar.querySelectorAll("button[data-theme-choice]");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        var c = this.getAttribute("data-theme-choice");
        if (c !== "light" && c !== "dark") c = "dark";
        setStored(c);
        applyTheme(c);
      });
    }
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", initTheme);
  else
    initTheme();
})();
</script>
"""

TOGGLE_JS = """
<script>
(function () {
  var KEY = "smsRipperTzDisplay";
  var COOKIE_MAX_AGE = 31536000;
  function getCookie(name) {
    var prefix = name + "=";
    var chunks = document.cookie.split(";");
    for (var i = 0; i < chunks.length; i++) {
      var p = chunks[i].replace(/^\\s+/, "");
      if (p.indexOf(prefix) === 0)
        return decodeURIComponent(p.substring(prefix.length));
    }
    return null;
  }
  function setCookie(name, value) {
    document.cookie =
      name +
      "=" +
      encodeURIComponent(value) +
      "; max-age=" +
      COOKIE_MAX_AGE +
      "; path=/; SameSite=Lax";
  }
  function parseISO(s) {
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function fmtUTC(d) {
    var y = d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate());
    var t =
      pad2(d.getUTCHours()) +
      ":" +
      pad2(d.getUTCMinutes()) +
      ":" +
      pad2(d.getUTCSeconds()) +
      " UTC";
    return y + "<br>" + t;
  }
  function tzAbbrevShort(d) {
    var abbr = "";
    try {
      var parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(d);
      for (var j = 0; j < parts.length; j++) {
        if (parts[j].type === "timeZoneName") {
          abbr = parts[j].value;
          break;
        }
      }
    } catch (e) {
      return "";
    }
    if (!abbr) return "";
    if (/GMT|UTC/i.test(abbr) && /[+-]\d/.test(abbr)) return "";
    if (/[+-]\d/.test(abbr)) return "";
    return abbr;
  }
  function fmtLocal(d) {
    try {
      var dateLine = new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric"
      }).format(d);
      var timeLine = new Intl.DateTimeFormat(undefined, {
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true
      }).format(d);
      var abbr = tzAbbrevShort(d);
      return dateLine + "<br>" + timeLine + (abbr ? " " + abbr : "");
    } catch (e) {
      return String(d);
    }
  }
  function apply(mode) {
    var els = document.querySelectorAll("[data-utc]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var raw = el.getAttribute("data-utc");
      if (!raw) continue;
      var d = parseISO(raw);
      if (!d) continue;
      el.innerHTML = mode === "local" ? fmtLocal(d) : fmtUTC(d);
    }
  }
  function setMode(mode) {
    var bar = document.getElementById("sms-ripper-tz-bar");
    if (!bar) return;
    var btns = bar.querySelectorAll("button[data-tz]");
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].getAttribute("data-tz") === mode;
      btns[i].classList.toggle("active", on);
      btns[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
    apply(mode);
  }
  function init() {
    var saved = getCookie(KEY);
    if (saved !== "utc" && saved !== "local") saved = "utc";
    var bar = document.getElementById("sms-ripper-tz-bar");
    if (!bar) return;
    var btns = bar.querySelectorAll("button[data-tz]");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        var mode = this.getAttribute("data-tz");
        setCookie(KEY, mode);
        setMode(mode);
      });
    }
    setMode(saved);
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else
    init();
})();
</script>
"""
