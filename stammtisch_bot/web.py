"""Kleine Weboberfläche: Passwort, Einstellungen, Status und Aktionen."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from html import escape
from typing import Awaitable, Callable

from aiohttp import web
from yarl import URL

from . import __version__
from .config import FIELDS, validate
from .runner import BotRunner
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
:root{--bg:#f6f5f2;--card:#fff;--fg:#1f2328;--mut:#6a6f76;--line:#e2e0db;--acc:#b7791f;--ok:#2f855a;--err:#c53030}
@media(prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1d2024;--fg:#e8e6e3;--mut:#9aa0a6;--line:#2c3036;--acc:#e0a23a;--ok:#5fbf8a;--err:#ef6b6b}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;align-items:center;gap:1.2rem;padding:.8rem 1.4rem;border-bottom:1px solid var(--line);background:var(--card)}
header b{font-size:1.05rem}header nav{display:flex;gap:1rem;flex:1}header a{color:var(--mut);text-decoration:none}
header a.on{color:var(--fg);font-weight:600}main{max-width:760px;margin:1.6rem auto;padding:0 1rem}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1.1rem 1.3rem;margin-bottom:1rem}
h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:0 0 .8rem}
label{display:block;font-weight:600;margin:.8rem 0 .25rem}small{display:block;color:var(--mut);font-weight:400;margin-top:.2rem}
input,textarea{width:100%;padding:.5rem .6rem;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
textarea{font-family:ui-monospace,monospace;font-size:12px;min-height:7rem}
button{padding:.55rem 1rem;border:0;border-radius:6px;background:var(--acc);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}
button:disabled{opacity:.35;cursor:not-allowed}
.row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}.grid{display:grid;grid-template-columns:auto 1fr;gap:.35rem 1.2rem}
.grid dt{color:var(--mut)}.grid dd{margin:0}.ok{color:var(--ok)}.err{color:var(--err)}
.msg{padding:.7rem 1rem;border-radius:8px;margin-bottom:1rem;border:1px solid var(--line);background:var(--card)}
.msg.err{border-color:var(--err)}.msg.ok{border-color:var(--ok)}.set{color:var(--ok);font-weight:400}
.big{font-size:2rem;font-weight:700;line-height:1}.stats{display:flex;gap:2rem;margin:.3rem 0 .8rem}.stats div small{margin:0}
.narrow{max-width:380px}
"""


def page(title: str, body: str, active: str = "", nav: bool = True) -> web.Response:
    links = "".join(f'<a href="{href}" class="{"on" if active == href else ""}">{name}</a>'
                    for href, name in (("/", "Status"), ("/settings", "Einstellungen")))
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


def redirect(path: str, **query: str) -> web.HTTPFound:
    url = URL(path).with_query(query) if query else URL(path)
    return web.HTTPFound(str(url))


# ------------------------------------------------------------------ Anwendung

def create_app(store: Store, runner: BotRunner) -> web.Application:
    routes = web.RouteTableDef()

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
        state_cls = "ok" if runner.state == "läuft" else "err"
        rows = [
            ("Bot", f'<span class="{state_cls}">{escape(runner.state)}</span>'
                    + (f' als @{escape(runner.username)}' if runner.username else "")),
            ("MQTT", '<span class="ok">verbunden</span>' if runner.mqtt_connected
                     else '<span class="err">nicht verbunden</span>'),
        ]
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
        <section><h2>Aktionen</h2><div class="row">
          <form method="post" action="/action/start" onsubmit="return confirm('Neue Umfrage in der Gruppe starten?')"><button {disabled}>Umfrage starten</button></form>
          <form method="post" action="/action/end" onsubmit="return confirm('Umfrage jetzt beenden und Ergebnis senden?')"><button {disabled}>Umfrage beenden</button></form>
          <form method="post" action="/action/status"><button class="ghost" {disabled}>Status in Gruppe senden</button></form>
          <form method="post" action="/action/restart"><button class="ghost">Bot neu starten</button></form>
        </div></section>
        <p><small>Version {__version__}</small></p>"""
        return page("Status", body, active="/")

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

