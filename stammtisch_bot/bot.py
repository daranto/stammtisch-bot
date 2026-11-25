"""Die eigentliche Stammtisch-Logik: Umfrage, Zählen, Nachzügler, Befehle."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, TypeVar

from telegram import Update
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter
from telegram.ext import Application, CommandHandler, ContextTypes, PollAnswerHandler, filters

from . import texts
from .config import Config
from .mqtt_ha import HomeAssistantMqtt
from .schedule import calendar_week, cycle, end_after
from .sheets import Sheets, fmt_de
from .store import Store

log = logging.getLogger(__name__)
T = TypeVar("T")

LATE_COOLDOWN_S = 2 * 60      # dieselbe Person höchstens alle 2 min aufrufen
ADMIN_COOLDOWN_S = 15 * 60    # Fehler höchstens alle 15 min an den Admin


class StammtischBot:
    def __init__(self, cfg: Config, app: Application, store: Store, sheets: Sheets, ha: HomeAssistantMqtt):
        self.cfg, self.app, self.store, self.sheets, self.ha = cfg, app, store, sheets, ha
        self._lock = asyncio.Lock()        # Start/Ende nie gleichzeitig
        self._admin_last: dict[str, float] = {}
        ha.on_command = self.on_mqtt_command

    # ------------------------------------------------------------------ Hilfen
    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    async def _retry(self, what: str, call: Callable[[], Awaitable[T]], attempts: int = 8) -> T:
        """Telegram-Aufruf mit Wiederholung bei Netzfehlern und Rate-Limit."""
        delay = 5.0
        for attempt in range(1, attempts + 1):
            try:
                return await call()
            except RetryAfter as e:
                wait = e.retry_after
                wait = wait.total_seconds() if isinstance(wait, timedelta) else float(wait)
                log.warning("%s: Rate-Limit, warte %.0f s", what, wait)
                await asyncio.sleep(wait + 1)
            except (BadRequest, Forbidden):
                raise  # wird durch Wiederholen nicht besser
            except NetworkError as e:
                if attempt == attempts:
                    raise
                log.warning("%s fehlgeschlagen (Versuch %d): %s – neuer Versuch in %.0f s", what, attempt, e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)
        raise RuntimeError(f"{what}: zu viele Versuche")

    async def say(self, *lines: str, html_mode: bool = False) -> None:
        """Nachrichten strikt nacheinander in die Gruppe senden."""
        for line in lines:
            await self._retry("Nachricht senden", lambda line=line: self.app.bot.send_message(
                self.cfg.chat_id, line, parse_mode="HTML" if html_mode else None))

    async def notify_admin(self, key: str, text: str) -> None:
        if not self.cfg.admin_chat_id:
            return
        now = time.time()
        if now - self._admin_last.get(key, 0) < ADMIN_COOLDOWN_S:
            return
        self._admin_last[key] = now
        try:
            await self.app.bot.send_message(self.cfg.admin_chat_id, f"⚠️ Stammtisch-Bot: {text}"[:4000])
        except Exception:  # noqa: BLE001 – Fehlermeldung darf nie selbst crashen
            log.exception("Admin-Benachrichtigung fehlgeschlagen")

    async def standort(self) -> str | None:
        if not self.sheets.enabled:
            return None
        try:
            value = await self.sheets.standort(self.now())
            self.store.set(standort=value)
            return value
        except Exception as e:  # noqa: BLE001
            log.warning("Standort-Abfrage fehlgeschlagen: %s", e)
            await self.notify_admin("sheet", f"Standort-Abfrage fehlgeschlagen: {e}")
            return None

    # ------------------------------------------------------------------ Home Assistant
    async def publish(self) -> None:
        p = self.store.poll()
        c = self.store.counts(p.poll_id)
        await self.ha.publish_state({
            "kw": p.kw if p.active else None,
            "active": "ON" if p.active else "OFF",
            "beer": c.beer,
            "nobeer": c.nobeer,
            "sum": c.total,
            "names_beer": c.names_beer,
            "names_nobeer": c.names_nobeer,
            "empfehlung": texts.biermenge(c.beer) or "–",
            "standort": self.store.get("standort"),
            "letzter_status": self.store.last_history_text() or "",
            "ends_at": p.ends_at if p.active else None,
            "message_id": p.message_id,
            "poll_id": p.poll_id,
            "late_caller": "OFF" if self.store.get("late_caller") == "0" else "ON",
        })

    async def on_mqtt_command(self, name: str, payload: str) -> None:
        log.info("Befehl aus Home Assistant: %s %s", name, payload)
        try:
            if name == "start":
                await self.start_poll(manual=True)
            elif name == "end":
                await self.end_poll()
            elif name == "status":
                await self.send_status()
            elif name == "late_caller":
                self.store.set(late_caller="1" if payload.upper() == "ON" else "0")
                await self.publish()
        except Exception as e:  # noqa: BLE001
            log.exception("Befehl %s fehlgeschlagen", name)
            await self.notify_admin(f"cmd_{name}", f"Befehl {name} aus HA fehlgeschlagen: {e}")

    # ------------------------------------------------------------------ Umfrage
    async def start_poll(self, manual: bool = False) -> None:
        async with self._lock:
            now = self.now()
            kw = calendar_week(now)
            p = self.store.poll()
            if p.active and not manual:
                log.info("Umfrage für KW %s läuft bereits", p.kw)
                return

            # alte Umfrage schließen (bis hierhin lief der Zu-spät-Caller)
            if p.message_id:
                try:
                    await self._retry("Alte Umfrage schließen",
                                      lambda: self.app.bot.stop_poll(self.cfg.chat_id, p.message_id))
                except BadRequest as e:
                    log.info("Alte Umfrage %s nicht geschlossen (%s) – vermutlich schon zu", p.message_id, e)

            msg = await self._retry("Umfrage senden", lambda: self.app.bot.send_poll(
                self.cfg.chat_id, texts.poll_question(kw), texts.POLL_OPTIONS,
                is_anonymous=False, allows_multiple_answers=False))

            ends = end_after(now, self.cfg.poll_start, self.cfg.poll_end)
            self.store.set(kw=kw, message_id=msg.message_id, poll_id=msg.poll.id, active="1",
                           started_at=now.isoformat(), ends_at=ends.isoformat())
            log.info("Umfrage KW %s gestartet (Message %s, Poll %s), endet %s",
                     kw, msg.message_id, msg.poll.id, ends)
        await self.standort()
        await self.publish()

    async def end_poll(self) -> None:
        async with self._lock:
            p = self.store.poll()
            if not p.active:
                log.info("Keine laufende Umfrage zum Beenden")
                return
            c = self.store.counts(p.poll_id)
            standort = await self.standort()
            now = self.now()
            verlauf = texts.verlauf(c.beer, c.nobeer, standort)

            # erst speichern, dann senden -> nach einem Absturz wird nichts doppelt verschickt
            self.store.add_history(p.poll_id, now.year, p.kw, now.isoformat(), c.beer, c.nobeer, standort, verlauf)
            self.store.set(active="0")
            log.info("Umfrage KW %s beendet: %s", p.kw, verlauf)
        await self.publish()

        lines = [
            "🟣 Stammtisch Umfrage ist beendet 🟣",
            f"🟡 Stammtisch Zusagen (🍺): {c.beer}",
            f"🟡 Stammtisch Zusagen (🚫🍺): {c.nobeer}",
            texts.bier_empfehlung(c.beer),
        ]
        if standort:
            lines.append(texts.standort_zeile(standort))
        await self.say(*lines)

    async def catch_up(self) -> None:
        """Nach einem Neustart verpasste Start-/End-Zeitpunkte nachholen."""
        now = self.now()
        p = self.store.poll()
        if p.active and p.ends_at and now >= datetime.fromisoformat(p.ends_at):
            log.info("Ende verpasst (%s) – hole nach", p.ends_at)
            await self.end_poll()
            p = self.store.poll()
        start, end = cycle(now, self.cfg.poll_start, self.cfg.poll_end)
        started = datetime.fromisoformat(p.started_at) if p.started_at else None
        if not p.active and start <= now < end and (started is None or started < start):
            log.info("Start verpasst (%s) – hole nach", start)
            await self.start_poll()

    # ------------------------------------------------------------------ Stimmen
    async def on_poll_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        a = update.poll_answer
        p = self.store.poll()
        if a is None or a.user is None or a.poll_id != p.poll_id:
            return  # alte/fremde Umfrage oder anonym im Namen eines Kanals
        user = a.user
        option = a.option_ids[0] if a.option_ids else None
        name = user.first_name or user.username or "Unbekannter"

        if p.active:
            self.store.set_vote(p.poll_id, user.id, name, option)
            log.info("Stimme KW %s: %s -> %s", p.kw, name, option)
            await self.publish()
            return

        # nach Umfrageschluss: Nachzügler aufrufen
        if self.store.get("late_caller") == "0":
            return
        kategorie = texts.zu_spaet_kategorie(list(a.option_ids))
        if not kategorie or not self.store.late_call_allowed(p.poll_id, user.id, LATE_COOLDOWN_S):
            return
        liste = texts.ZU_SPAET[kategorie]
        last = self.store.get(f"zuletzt_{kategorie}")
        i = random.randrange(len(liste))
        if len(liste) > 1 and last is not None and i == int(last):
            i = (i + 1) % len(liste)
        self.store.set(**{f"zuletzt_{kategorie}": i})
        mention = f'<a href="tg://user?id={user.id}">{html.escape(name)}</a>'
        await self.say(html.escape(liste[i]).replace("{name}", mention), html_mode=True)

    # ------------------------------------------------------------------ Befehle
    async def send_status(self) -> None:
        p = self.store.poll()
        c = self.store.counts(p.poll_id)
        if p.active and c.total > 0:
            standort = await self.standort()
            lines = [
                f"🟡 Stammtisch Zusage(n) (🍺): {c.beer}",
                f"🟡 Stammtisch Zusage(n) (🚫🍺): {c.nobeer}",
                texts.bier_empfehlung(c.beer),
            ]
            if standort:
                lines.append(texts.standort_zeile(standort))
            await self.say(*lines)
        else:
            await self.say(texts.KEIN_STATUS, self.store.last_history_text() or "Kein letzter Status vorhanden")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.send_status()

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.say(*texts.HILFE)

    async def cmd_liste(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.sheets.url:
            await self.say(self.sheets.url)

    async def cmd_song(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.say(texts.MUSIK)

    async def cmd_bier(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.cfg.openai_api_key:
            return
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.cfg.openai_api_key, timeout=60)
        resp = await client.chat.completions.create(
            model=self.cfg.openai_model, messages=[{"role": "user", "content": texts.HERBERT_PROMPT}])
        text = (resp.choices[0].message.content or "").strip()
        if text:
            await self.say(text)

    async def cmd_menge(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        liter = await self.sheets.liters()
        await self.say(f"Es wurden schon {fmt_de(liter)}l Bier getrunken")

    async def cmd_bagger(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        rest = self.cfg.bagger_ziel - await self.sheets.kasse()
        await self.say(f"Es fehlen noch {fmt_de(rest, 2)} € zum Stammtisch Bagger.")

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        log.error("Fehler bei Update %s", update, exc_info=err)
        await self.notify_admin(type(err).__name__, f"{type(err).__name__}: {err}")

    # ------------------------------------------------------------------ Verdrahtung
    def register(self) -> None:
        group = filters.Chat(self.cfg.chat_id)  # nur Befehle aus der Stammtisch-Gruppe
        commands: dict[str, Callable[..., Any]] = {
            "s": self.cmd_status, "h": self.cmd_help, "l": self.cmd_liste, "song": self.cmd_song,
            "bier": self.cmd_bier, "m": self.cmd_menge, "bagger": self.cmd_bagger,
        }
        for name, handler in commands.items():
            self.app.add_handler(CommandHandler(name, handler, filters=group))
        self.app.add_handler(PollAnswerHandler(self.on_poll_answer))
        self.app.add_error_handler(self.on_error)

        jq = self.app.job_queue
        tz = self.cfg.tz
        # JobQueue zählt Wochentage ab Sonntag = 0, unsere Config ab Montag = 0
        to_jq = lambda wd: (wd + 1) % 7  # noqa: E731
        start, end = self.cfg.poll_start, self.cfg.poll_end
        jq.run_daily(self._job_start, start.at.replace(tzinfo=tz), days=(to_jq(start.weekday),), name="start")
        jq.run_daily(self._job_end, end.at.replace(tzinfo=tz), days=(to_jq(end.weekday),), name="end")
        jq.run_repeating(self._job_refresh, interval=3600, first=60, name="refresh")

    async def _job_start(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._run_job("Umfrage starten", self.start_poll)

    async def _job_end(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._run_job("Umfrage beenden", self.end_poll)

    async def _job_refresh(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        # stündlich: fehlgeschlagenen Start/Ende nachholen, Standort aktualisieren
        await self._run_job("Nachholen", self.catch_up)
        await self.standort()
        await self.publish()

    async def _run_job(self, what: str, job: Callable[[], Awaitable[None]]) -> None:
        try:
            await job()
        except Exception as e:  # noqa: BLE001
            log.exception("%s fehlgeschlagen", what)
            await self.notify_admin(what, f"{what} fehlgeschlagen: {e}")
