"""Kleine Weboberfläche: Passwort, Einstellungen, Status und Aktionen."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo
from html import escape
from typing import Awaitable, Callable

from aiohttp import web
from yarl import URL

from . import __version__
from .config import DAY_NAMES, FIELDS, WeeklyTime, validate
from .checks import Check, run_checks
from .runner import CONFLICT_HINT, BotRunner
from .texts import TEXT_FIELDS, Texts, parse_rules, validate_texts
from . import updater
from .store import Store

COOKIE = "stammtisch_session"
SESSION_S = 30 * 24 * 3600
PUBLIC = {"/login", "/setup", "/style.css"}


# ------------------------------------------------------------------ Passwort & Sitzung

def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def check_password(password: str, stored: str) -> bool:
    salt, _ = stored.split(":", 1)
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), stored)


def _secret(store: Store) -> bytes:
    s = store.get("web_secret")
    if not s:
        s = secrets.token_hex(32)
        store.set(web_secret=s)
    return bytes.fromhex(s)


def make_session(store: Store) -> str:
    expires = str(int(time.time()) + SESSION_S)
    sig = hmac.new(_secret(store), expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def valid_session(store: Store, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    good = hmac.new(_secret(store), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and expires.isdigit() and int(expires) > time.time()


# ------------------------------------------------------------------ HTML

CSS = """
:root{color-scheme:light dark;--bg:#f6f5f2;--card:#fff;--fg:#1f2328;--mut:#6a6f76;--line:#e2e0db;--acc:#b7791f;--ok:#2f855a;--err:#c53030}
@media(prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1d2024;--fg:#e8e6e3;--mut:#9aa0a6;--line:#2c3036;--acc:#e0a23a;--ok:#5fbf8a;--err:#ef6b6b}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;align-items:center;gap:1.2rem;padding:.8rem 1.4rem;border-bottom:1px solid var(--line);background:var(--card)}
header b{font-size:1.05rem}header nav{display:flex;gap:1rem;flex:1}header a{color:var(--mut);text-decoration:none}
header a.on{color:var(--fg);font-weight:600}main{max-width:760px;margin:1.6rem auto;padding:0 1rem}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1.1rem 1.3rem;margin-bottom:1rem}
h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:0 0 .8rem}
label{display:block;font-weight:600;margin:.8rem 0 .25rem}small{display:block;color:var(--mut);font-weight:400;margin-top:.2rem}
input,textarea,select{width:100%;padding:.5rem .6rem;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
textarea{font-family:ui-monospace,monospace;font-size:12px;min-height:7rem}
button{padding:.55rem 1rem;border:0;border-radius:6px;background:var(--acc);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}
button:disabled{opacity:.35;cursor:not-allowed}
.row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}.grid{display:grid;grid-template-columns:auto 1fr;gap:.35rem 1.2rem}
.grid dt{color:var(--mut)}.grid dd{margin:0}.ok{color:var(--ok)}.err{color:var(--err)}
.msg{padding:.7rem 1rem;border-radius:8px;margin-bottom:1rem;border:1px solid var(--line);background:var(--card)}
.msg.err{border-color:var(--err)}.msg.ok{border-color:var(--ok)}.set{color:var(--ok);font-weight:400}
.warn{color:var(--acc)}.mut{color:var(--mut)}.checks dt{white-space:nowrap}.checks dd{overflow-wrap:anywhere}
.big{font-size:2rem;font-weight:700;line-height:1}.stats{display:flex;gap:2rem;margin:.3rem 0 .8rem}.stats div small{margin:0}
.narrow{max-width:380px}
table.rules{width:100%;border-collapse:collapse;margin-top:.3rem}table.rules th{text-align:left;font-size:.75rem;color:var(--mut);font-weight:600;padding:0 .4rem .3rem}
table.rules td{padding:.25rem .4rem;vertical-align:middle}table.rules td:first-child{width:3.5rem;color:var(--mut);font-variant-numeric:tabular-nums}
table.rules tr.fixed td{color:var(--mut)}table.rules input.bis{width:6rem}table.rules td:last-child{width:2.5rem}
button.x{background:transparent;color:var(--mut);border:0;padding:.3rem .5rem;font-size:1rem}button.x:hover{color:var(--err)}
input.bad{border-color:var(--err)!important}
.seg{display:flex;gap:.5rem;flex-wrap:wrap}.seg label{margin:0;font-weight:400;cursor:pointer}.seg input{position:absolute;opacity:0;width:0}
.seg span{display:inline-block;padding:.5rem .9rem;border:1px solid var(--line);border-radius:8px;background:var(--bg)}
.seg input:checked+span{border-color:var(--acc);box-shadow:inset 0 0 0 1px var(--acc);font-weight:600}.seg input:focus-visible+span{outline:2px solid var(--acc)}
textarea.txt{font-family:inherit;font-size:14px}ol.preview{margin:.3rem 0 1rem;padding-left:1.4rem}ol.preview li{margin:.15rem 0}
pre.log{background:var(--bg);padding:.8rem;border-radius:6px;overflow:auto;max-height:24rem;font-size:12px;white-space:pre-wrap}code{font-family:ui-monospace,monospace}
.pair{display:flex;gap:.6rem}.pair select{flex:1}.pair input{width:9rem}
"""


def page(title: str, body: str, active: str = "", nav: bool = True) -> web.Response:
    links = "".join(f'<a href="{href}" class="{"on" if active == href else ""}">{name}</a>'
                    for href, name in (("/", "Status"), ("/settings", "Einstellungen"), ("/texts", "Texte & Regeln"),
                                       ("/update", "Update")))
    head = (f'<header><b>🍺 Stammtisch-Bot</b><nav>{links}</nav>'
            f'<form method="post" action="/logout"><button class="ghost">Abmelden</button></form></header>'
            if nav else '<header><b>🍺 Stammtisch-Bot</b></header>')
    html = (f'<!doctype html><html lang="de"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)} · Stammtisch-Bot</title><link rel="stylesheet" href="/style.css"></head>'
            f'<body>{head}<main>{body}</main></body></html>')
    return web.Response(text=html, content_type="text/html")


def flash(request: web.Request) -> str:
    ok, err = request.query.get("ok"), request.query.get("err")
    if ok:
        return f'<div class="msg ok">{escape(ok)}</div>'
    if err:
        return f'<div class="msg err">{escape(err)}</div>'
    return ""


@lru_cache(maxsize=1)
def timezones() -> list[str]:
    from zoneinfo import available_timezones
    zones = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/")))
    return zones or ["Europe/Berlin"]


def weekly_input(key: str, value: str) -> str:
    try:
        wt = WeeklyTime.parse(value)
    except ValueError:
        wt = WeeklyTime.parse("di 12:00")
    days = "".join(f'<option value="{code}"{" selected" if i == wt.weekday else ""}>{name}</option>'
                   for i, (code, name) in enumerate(DAY_NAMES))
    return (f'<div class="pair"><select name="{key}_day">{days}</select>'
            f'<input type="time" name="{key}_time" value="{wt.at.strftime("%H:%M")}" required></div>')


def tz_select(value: str) -> str:
    zones = timezones() if value in timezones() else [value, *timezones()]
    opts = "".join(f'<option{" selected" if z == value else ""}>{escape(z)}</option>' for z in zones)
    return f'<select name="tz">{opts}</select>'


def rules_editor(name: str, value: str, default: str) -> str:
    """Biermengen-Regeln als Stufen-Tabelle; schreibt beim Tippen das Textformat ins versteckte Feld."""
    try:
        rules, fallback = parse_rules(value)
    except ValueError:
        rules, fallback = parse_rules(default)
    rows = "".join(
        f'<tr class="rule"><td class="von"></td>'
        f'<td><input type="number" class="bis" min="1" value="{limit}" aria-label="bis Personen"></td>'
        f'<td><input class="amt" value="{escape(amount)}" placeholder="z. B. 10l" aria-label="Menge"></td>'
        f'<td><button type="button" class="x" title="Stufe entfernen">✕</button></td></tr>'
        for limit, amount in rules)
    return f"""
<table class="rules" id="rules">
  <thead><tr><th>Von</th><th>Bis Personen</th><th>Empfohlene Menge</th><th></th></tr></thead>
  <tbody>
    <tr class="fixed"><td colspan="2">0 Personen</td><td>→ Ausfall-Text (siehe unten)</td><td></td></tr>
    {rows}
    <tr class="fixed" id="ab-row"><td colspan="2" id="ab"></td>
      <td><input class="fb" value="{escape(fallback or "")}" placeholder="z. B. 30l+" aria-label="Menge darüber"></td><td></td></tr>
  </tbody>
</table>
<p><button type="button" class="ghost" id="add-rule">+ Stufe hinzufügen</button></p>
<p class="err" id="rules-err" hidden>Jede Stufe braucht eine höhere „Bis“-Zahl als die vorige und eine Menge.</p>
<input type="hidden" name="{name}" id="rules-value" value="{escape(value)}">
<script>
(() => {{
  const t = document.getElementById("rules"), body = t.tBodies[0], abRow = document.getElementById("ab-row");
  const hidden = document.getElementById("rules-value"), err = document.getElementById("rules-err");
  function sync() {{
    let prev = 0, ok = true; const lines = [];
    body.querySelectorAll("tr.rule").forEach(r => {{
      const bisEl = r.querySelector(".bis"), amtEl = r.querySelector(".amt");
      const bis = parseInt(bisEl.value, 10), amt = amtEl.value.trim();
      r.querySelector(".von").textContent = prev + 1;
      const bad = !(bis > prev);
      bisEl.classList.toggle("bad", bad); amtEl.classList.toggle("bad", !amt);
      if (bad || !amt) ok = false;
      if (bis > prev) prev = bis;
      lines.push(bisEl.value + " = " + amt);
    }});
    document.getElementById("ab").textContent = "ab " + (prev + 1) + " Personen";
    const fb = abRow.querySelector(".fb").value.trim();
    if (fb) lines.push("sonst = " + fb);
    hidden.value = lines.join("\\n");
    err.hidden = ok;
    return ok;
  }}
  t.addEventListener("input", sync);
  t.addEventListener("click", e => {{
    if (e.target.classList.contains("x")) {{ e.target.closest("tr").remove(); sync(); }}
  }});
  document.getElementById("add-rule").addEventListener("click", () => {{
    const last = [...body.querySelectorAll("tr.rule .bis")].map(i => parseInt(i.value, 10) || 0);
    const next = (last.length ? Math.max(...last) : 0) + 3;
    const tr = document.createElement("tr"); tr.className = "rule";
    tr.innerHTML = '<td class="von"></td><td><input type="number" class="bis" min="1" value="' + next +
      '"></td><td><input class="amt" placeholder="z. B. 10l"></td><td><button type="button" class="x" title="Stufe entfernen">✕</button></td>';
    body.insertBefore(tr, abRow); sync(); tr.querySelector(".amt").focus();
  }});
  t.closest("form").addEventListener("submit", e => {{ if (!sync()) {{ e.preventDefault(); err.scrollIntoView({{block: "center"}}); }} }});
  sync();
}})();
</script>"""


def redirect(path: str, **query: str) -> web.HTTPFound:
    url = URL(path).with_query(query) if query else URL(path)
    return web.HTTPFound(str(url))


# ------------------------------------------------------------------ Anwendung

def _tz(store: Store) -> ZoneInfo:
    try:
        return ZoneInfo(store.settings().get("tz") or "Europe/Berlin")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Europe/Berlin")


def render_checks(last: tuple[float, list] | None, tz: ZoneInfo) -> str:
    intro = ('<p><small>Prüft Telegram, Gruppe, MQTT, Google Sheet und ChatGPT. '
             'In der Gruppe wird dabei nichts geschrieben.</small></p>')
    button = ('<form method="post" action="/test" onsubmit="const b=this.querySelector(`button`);b.textContent=`Prüfe …`;b.disabled=true"><button class="ghost">Jetzt prüfen</button></form>')
    if not last:
        return f'<section id="test"><h2>Verbindungstest</h2>{intro}{button}</section>'
    ts, checks = last
    icons = {"ok": '<span class="ok">✓</span>', "fehler": '<span class="err">✗</span>',
             "hinweis": '<span class="warn">!</span>', "aus": '<span class="mut">–</span>'}
    rows = "".join(f"<dt>{icons.get(c.status, '?')} {escape(c.name)}</dt><dd>{escape(c.detail)}</dd>" for c in checks)
    bad = sum(c.status == "fehler" for c in checks)
    summary = ('<p class="ok"><b>Alles bereit.</b></p>' if not bad
               else f'<p class="err"><b>{bad} Problem{"e" if bad > 1 else ""} gefunden.</b></p>')
    when = datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S")
    return (f'<section id="test"><h2>Verbindungstest · {when}</h2>{summary}'
            f'<dl class="grid checks">{rows}</dl><p>{button}</p></section>')


def create_app(store: Store, runner: BotRunner) -> web.Application:
    routes = web.RouteTableDef()
    last_checks: list[tuple[float, list] | None] = [None]

    @web.middleware
    async def auth(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
        if request.path not in PUBLIC:
            if not store.get("web_password"):
                raise redirect("/setup")
            if not valid_session(store, request.cookies.get(COOKIE)):
                raise redirect("/login")
        resp = await handler(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def login_response(path: str) -> web.HTTPFound:
        resp = redirect(path)
        resp.set_cookie(COOKIE, make_session(store), max_age=SESSION_S, httponly=True, samesite="Strict")
        return resp

    @routes.get("/style.css")
    async def style(request: web.Request) -> web.Response:
        return web.Response(text=CSS, content_type="text/css")

    # --- Erstes Passwort ---------------------------------------------------
    @routes.get("/setup")
    async def setup_form(request: web.Request) -> web.Response:
        if store.get("web_password"):
            raise redirect("/login")
        return page("Einrichten", f"""{flash(request)}<section class="narrow">
            <h2>Willkommen</h2><p>Lege ein Passwort für diese Seite fest.</p>
            <form method="post"><label>Passwort</label><input type="password" name="pw" minlength="8" required autofocus>
            <label>Wiederholen</label><input type="password" name="pw2" required>
            <p><button>Speichern</button></p></form></section>""", nav=False)

    @routes.post("/setup")
    async def setup_save(request: web.Request) -> web.Response:
        if store.get("web_password"):
            raise redirect("/login")
        form = await request.post()
        pw, pw2 = str(form.get("pw", "")), str(form.get("pw2", ""))
        if len(pw) < 8 or pw != pw2:
            raise redirect("/setup", err="Passwörter stimmen nicht überein oder sind kürzer als 8 Zeichen")
        store.set(web_password=hash_password(pw))
        raise login_response("/settings")

    # --- Anmelden -------------------------------------------------------------
    @routes.get("/login")
    async def login_form(request: web.Request) -> web.Response:
        return page("Anmelden", f"""{flash(request)}<section class="narrow"><h2>Anmelden</h2>
            <form method="post"><label>Passwort</label><input type="password" name="pw" required autofocus>
            <p><button>Anmelden</button></p></form></section>""", nav=False)

    @routes.post("/login")
    async def login(request: web.Request) -> web.Response:
        form = await request.post()
        stored = store.get("web_password")
        if stored and check_password(str(form.get("pw", "")), stored):
            raise login_response("/")
        await asyncio.sleep(1.5)  # bremst Durchprobieren
        raise redirect("/login", err="Falsches Passwort")

    @routes.post("/logout")
    async def logout(request: web.Request) -> web.Response:
        resp = redirect("/login")
        resp.del_cookie(COOKIE)
        raise resp

    # --- Status ----------------------------------------------------------------
    @routes.get("/")
    async def status(request: web.Request) -> web.Response:
        p = store.poll()
        c = store.counts(p.poll_id)
        conflict = getattr(runner, "conflict", False)
        state_cls = "ok" if runner.state == "läuft" and not conflict else "err"
        bot_state = "Konflikt: Token wird woanders genutzt" if conflict else runner.state
        rows = [
            ("Bot", f'<span class="{state_cls}">{escape(bot_state)}</span>'
                    + (f' als @{escape(runner.username)}' if runner.username else "")),
            ("MQTT", '<span class="ok">verbunden</span>' if runner.mqtt_connected
                     else '<span class="err">nicht verbunden</span>'),
        ]
        if conflict:
            minuten = max(1, round((time.time() - runner.conflict_since) / 60))
            rows.append(("Konflikt", f'<span class="err">seit {minuten} Min.: {escape(CONFLICT_HINT)}</span>'))
        if runner.error:
            rows.append(("Meldung", f'<span class="err">{escape(runner.error)}</span>'))
        rows += [
            ("Umfrage", f"läuft (KW {p.kw})" if p.active else "keine laufende Umfrage"),
            ("Standort", escape(store.get("standort") or "–")),
            ("Zu-spät-Caller", "aus" if store.get("late_caller") == "0" else "an"),
            ("Letzter Status", escape(store.last_history_text() or "–")),
        ]
        grid = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
        names = lambda xs: escape(", ".join(xs)) or "–"  # noqa: E731
        disabled = "" if runner.bot else "disabled"
        body = f"""{flash(request)}
        <section><h2>Zusagen {"(laufende Umfrage)" if p.active else "(letzte Umfrage)"}</h2>
          <div class="stats"><div><div class="big">{c.beer}</div><small>mit Bier 🍺</small></div>
          <div><div class="big">{c.nobeer}</div><small>ohne Bier 🚫🍺</small></div>
          <div><div class="big">{c.total}</div><small>gesamt</small></div></div>
          <dl class="grid"><dt>🍺</dt><dd>{names(c.names_beer)}</dd><dt>🚫🍺</dt><dd>{names(c.names_nobeer)}</dd></dl>
        </section>
        <section><h2>Zustand</h2><dl class="grid">{grid}</dl></section>
        {render_checks(last_checks[0], _tz(store))}
        <section><h2>Aktionen</h2><div class="row">
          <form method="post" action="/action/start" onsubmit="return confirm('Neue Umfrage in der Gruppe starten?')"><button {disabled}>Umfrage starten</button></form>
          <form method="post" action="/action/end" onsubmit="return confirm('Umfrage jetzt beenden und Ergebnis senden?')"><button {disabled}>Umfrage beenden</button></form>
          <form method="post" action="/action/status"><button class="ghost" {disabled}>Status in Gruppe senden</button></form>
          <form method="post" action="/action/restart"><button class="ghost">Bot neu starten</button></form>
        </div></section>
        <p><small>Version {__version__}</small></p>"""
        return page("Status", body, active="/")

    @routes.post("/test")
    async def test(request: web.Request) -> web.Response:
        checks = await run_checks(store, runner.mqtt_connected)
        if getattr(runner, "conflict", False):
            checks.insert(0, Check("Telegram-Abruf", "fehler", CONFLICT_HINT))
        last_checks[0] = (time.time(), checks)
        raise web.HTTPFound("/#test")

    @routes.post("/action/{name}")
    async def action(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name == "restart":
            await runner.restart()
            raise redirect("/", ok="Bot wird neu gestartet")
        if not runner.bot:
            raise redirect("/", err="Bot läuft nicht")
        actions = {"start": (lambda: runner.bot.start_poll(manual=True), "Umfrage gestartet"),
                   "end": (runner.bot.end_poll, "Umfrage beendet"),
                   "status": (runner.bot.send_status, "Status gesendet")}
        if name not in actions:
            raise web.HTTPNotFound()
        call, ok = actions[name]
        try:
            await call()
        except Exception as e:  # noqa: BLE001
            raise redirect("/", err=f"Fehlgeschlagen: {type(e).__name__}: {e}")
        raise redirect("/", ok=ok)

    # --- Einstellungen -----------------------------------------------------------
    @routes.get("/settings")
    async def settings_form(request: web.Request) -> web.Response:
        values = store.settings()
        parts, section = [], None
        for f in FIELDS:
            if f.section != section:
                if section is not None:
                    parts.append("</section>")
                parts.append(f"<section><h2>{escape(f.section)}</h2>")
                section = f.section
            current = values.get(f.key, "")
            req = " *" if f.required else ""
            if f.kind in ("secret", "json"):
                hint = ""
                if current:
                    hint = '<span class="set">✓ gesetzt</span>'
                    if f.kind == "json":
                        try:
                            hint = f'<span class="set">✓ {escape(json.loads(current)["client_email"])}</span>'
                        except (ValueError, KeyError):
                            pass
                placeholder = "leer lassen = unverändert" if current else ""
                clear = (f'<label style="font-weight:400;display:inline"><input type="checkbox" name="clear_{f.key}" '
                         f'style="width:auto"> löschen</label>') if current and not f.required else ""
                field = (f'<textarea name="{f.key}" placeholder="{placeholder}"></textarea>' if f.kind == "json"
                         else f'<input type="password" name="{f.key}" placeholder="{placeholder}" autocomplete="new-password">')
                parts.append(f'<label>{escape(f.label)}{req} {hint}</label>{field}{clear}')
            elif f.kind == "weekly":
                parts.append(f'<label>{escape(f.label)}{req}</label>{weekly_input(f.key, current or f.default)}')
            elif f.kind == "tz":
                parts.append(f'<label>{escape(f.label)}{req}</label>{tz_select(current or f.default)}')
            else:
                parts.append(f'<label>{escape(f.label)}{req}</label>'
                             f'<input name="{f.key}" value="{escape(current or f.default)}">')
            if f.help:
                parts.append(f"<small>{escape(f.help)}</small>")
        parts.append("</section>")
        body = (f'{flash(request)}<form method="post">{"".join(parts)}'
                f'<p class="row"><button>Speichern &amp; Bot neu starten</button></p></form>'
                f'<section><h2>Passwort ändern</h2><form method="post" action="/password">'
                f'<label>Neues Passwort</label><input type="password" name="pw" minlength="8" required>'
                f'<p><button class="ghost">Ändern</button></p></form></section>')
        return page("Einstellungen", body, active="/settings")

    @routes.post("/settings")
    async def settings_save(request: web.Request) -> web.Response:
        form = await request.post()
        old = store.settings()
        new: dict[str, str] = {}
        for f in FIELDS:
            v = str(form.get(f.key, "")).strip()
            if f.kind == "weekly":
                day, at = str(form.get(f"{f.key}_day", "")), str(form.get(f"{f.key}_time", ""))[:5]
                v = f"{day} {at}" if day and at else ""
            if f.kind in ("secret", "json"):
                if form.get(f"clear_{f.key}"):
                    v = ""
                elif not v:
                    v = old.get(f.key, "")   # leer = unverändert
            new[f.key] = v
        errors = validate(new)
        if errors:
            raise redirect("/settings", err="Nicht gespeichert: " + "; ".join(errors))
        store.save_settings(new)
        await runner.restart()
        raise redirect("/", ok="Gespeichert – Bot startet neu")

    # --- Texte & Regeln -------------------------------------------------------------
    @routes.get("/texts")
    async def texts_form(request: web.Request) -> web.Response:
        saved = store.settings()
        parts, group = [], None
        for f in TEXT_FIELDS:
            if f.group != group:
                if group is not None:
                    parts.append("</section>")
                parts.append(f"<section><h2>{escape(f.group)}</h2>")
                group = f.group
            value = saved.get(f.key) or f.default
            changed = ' <span class="warn">· geändert</span>' if saved.get(f.key) and saved[f.key] != f.default else ""
            parts.append(f"<label>{escape(f.label)}{changed}</label>")
            if f.key == "t_menge_regeln":
                parts.append(rules_editor(f.key, value, f.default))
            elif f.kind == "select" and len(f.choices) <= 3:
                parts.append('<div class="seg">' + "".join(
                    f'<label><input type="radio" name="{f.key}" value="{c}"{" checked" if c == value else ""}>'
                    f'<span>{escape(n)}</span></label>' for c, n in f.choices) + "</div>")
            elif f.kind in ("multi", "list"):
                rows = max(3, min(14, value.count("\n") + 2))
                parts.append(f'<textarea name="{f.key}" rows="{rows}" class="txt">{escape(value)}</textarea>')
            elif f.kind == "select":
                opts = "".join(f'<option value="{c}"{" selected" if c == value else ""}>{escape(n)}</option>'
                               for c, n in f.choices)
                parts.append(f'<select name="{f.key}">{opts}</select>')
            elif f.kind == "number":
                parts.append(f'<input name="{f.key}" value="{escape(value)}" inputmode="decimal" style="width:8rem">')
            else:
                parts.append(f'<input name="{f.key}" value="{escape(value)}">')
            hints = " · ".join(x for x in (f.help, f"Platzhalter: {f.placeholders}" if f.placeholders else "") if x)
            if hints:
                parts.append(f"<small>{escape(hints)}</small>")
        parts.append("</section>")

        # Vorschau mit Beispielwerten
        try:
            t = Texts(saved)
            mengen = "".join(f"<dt>{n} {'Person' if n == 1 else 'Personen'}</dt><dd>{escape(t.menge(n) or t.v['t_ausfall'])}</dd>"
                             for n in (0, 1, 3, 4, 6, 7, 12, 13, 20))
            vals = t.ergebnis(48, 5, 2, "Kalle", ["Anna", "Ben", "Cem", "Dora", "Eva"], ["Finn", "Gül"])
            ende = "".join(f"<li>{escape(x)}</li>" for x in t.lines("t_ende", vals))
            basis = "Zusagen mit Bier" if t.v["t_menge_basis"] == "bier" else "allen Zusagen"
            preview = (f'<section><h2>Vorschau (gespeicherter Stand)</h2>'
                       f'<p><b>Biermenge</b> <small style="display:inline">nach {basis}</small></p><dl class="grid">{mengen}</dl>'
                       f'<p><b>Umfrage beendet</b> <small style="display:inline">mit 5 🍺, 2 🚫🍺, Standort „Kalle“</small></p>'
                       f'<ol class="preview">{ende}</ol>'
                       f'<form method="post" action="/texts/send-preview"><button class="ghost" {"" if runner.bot else "disabled"}>'
                       f'Vorschau an Admin-Chat senden</button></form></section>')
        except ValueError as e:
            preview = f'<section><h2>Vorschau</h2><p class="err">{escape(str(e))}</p></section>'

        body = (f'{flash(request)}{preview}<form method="post">{"".join(parts)}'
                f'<p class="row"><button>Speichern &amp; Bot neu starten</button></p></form>'
                f'<section><h2>Zurücksetzen</h2><form method="post" action="/texts/reset" '
                f'onsubmit="return confirm(`Alle Texte und Regeln auf Standard zurücksetzen?`)">'
                f'<button class="ghost">Alles auf Standard</button></form></section>')
        return page("Texte & Regeln", body, active="/texts")

    @routes.post("/texts")
    async def texts_save(request: web.Request) -> web.Response:
        form = await request.post()
        new = {}
        for f in TEXT_FIELDS:
            v = str(form.get(f.key, "")).replace("\r\n", "\n").strip()
            new[f.key] = "" if v == f.default.strip() else v   # Standard nicht speichern
        errors = validate_texts(new)
        if errors:
            raise redirect("/texts", err="Nicht gespeichert: " + "; ".join(errors))
        store.save_settings(new)
        await runner.restart()
        raise redirect("/texts", ok="Gespeichert – Bot startet neu")

    @routes.post("/texts/reset")
    async def texts_reset(request: web.Request) -> web.Response:
        store.save_settings({f.key: "" for f in TEXT_FIELDS})
        await runner.restart()
        raise redirect("/texts", ok="Alle Texte und Regeln auf Standard zurückgesetzt")

    @routes.post("/texts/send-preview")
    async def texts_send_preview(request: web.Request) -> web.Response:
        bot = runner.bot
        if not bot or not bot.cfg.admin_chat_id:
            raise redirect("/texts", err="Dafür muss der Bot laufen und eine Admin-Chat-ID eingetragen sein")
        t = bot.t
        vals = t.ergebnis(48, 5, 2, "Kalle", ["Anna", "Ben"], ["Finn"])
        msgs = ["🧪 Vorschau – Umfrage:", t.question(48) + "\n" + " | ".join(t.options),
                "🧪 Vorschau – Umfrage beendet:", *t.lines("t_ende", vals)]
        late = t.late_texts("bier")
        try:
            for m in msgs:
                await bot.app.bot.send_message(bot.cfg.admin_chat_id, m)
            if late:
                await bot.app.bot.send_message(bot.cfg.admin_chat_id, "🧪 Vorschau – Nachzügler:")
                await bot.app.bot.send_message(bot.cfg.admin_chat_id, Texts.late_message(late[0], None, "Anna"),
                                               parse_mode="HTML")
        except Exception as e:  # noqa: BLE001
            raise redirect("/texts", err=f"Senden fehlgeschlagen: {e}")
        raise redirect("/texts", ok="Vorschau an den Admin-Chat gesendet")

    # --- Update -------------------------------------------------------------------------
    @routes.get("/update")
    async def update_page(request: web.Request) -> web.Response:
        info = await updater.status()
        running = info.running
        refresh = '<meta http-equiv="refresh" content="4">' if running else ""
        versions = (f'<dl class="grid"><dt>Installiert</dt><dd>{escape((info.installed or "?")[:7])} · Version {__version__}</dd>'
                    f'<dt>Auf GitHub</dt><dd>{escape((info.latest or "?")[:7])}'
                    f'{" – " + escape(info.latest_message) if info.latest_message else ""}</dd></dl>')
        if not info.supported:
            box = ('<p class="err">Update aus der Weboberfläche ist in dieser Installation noch nicht eingerichtet. '
                   'Einmalig im Container <code>update</code> ausführen – danach geht es hier.</p>' + versions)
        elif running:
            box = '<p><b>Update läuft …</b> Die Seite lädt sich selbst neu, der Bot startet danach neu.</p>'
        else:
            if info.latest and info.installed and info.latest.startswith(info.installed[:7]):
                state = '<p class="ok"><b>Aktuell.</b></p>'
            elif info.latest:
                state = (f'<p class="warn"><b>Neue Version verfügbar:</b> {escape(info.latest[:7])} – '
                         f'{escape(info.latest_message or "")}</p>')
            else:
                state = f'<p class="mut">Neueste Version nicht abrufbar ({escape(info.error or "?")})</p>'
            box = (f'{state}{versions}'
                   f'<form method="post" action="/update" onsubmit="return confirm(`Jetzt aktualisieren? Der Bot startet dabei kurz neu.`)">'
                   f'<p><button>Jetzt aktualisieren</button></p></form>')
        log_html = f'<section><h2>Letztes Update-Protokoll</h2><pre class="log">{escape(info.log)}</pre></section>' if info.log else ""
        return page("Update", f'{refresh}{flash(request)}<section><h2>Update</h2>{box}</section>{log_html}', active="/update")

    @routes.post("/update")
    async def update_start(request: web.Request) -> web.Response:
        try:
            updater.request_update()
        except OSError as e:
            raise redirect("/update", err=f"Konnte Update nicht anstoßen: {e}")
        raise redirect("/update", ok="Update angestoßen")

    @routes.post("/password")
    async def change_password(request: web.Request) -> web.Response:
        pw = str((await request.post()).get("pw", ""))
        if len(pw) < 8:
            raise redirect("/settings", err="Passwort zu kurz (mind. 8 Zeichen)")
        store.set(web_password=hash_password(pw), web_secret=secrets.token_hex(32))  # alte Sitzungen ungültig
        raise login_response("/settings")

    app = web.Application(middlewares=[auth])
    app.add_routes(routes)
    return app

