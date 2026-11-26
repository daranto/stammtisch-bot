"""Startet/stoppt Bot und MQTT anhand der gespeicherten Einstellungen (Neustart beim Speichern)."""
from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update
from telegram.error import Conflict, InvalidToken, TelegramError
from telegram.ext import AIORateLimiter, Application, ApplicationBuilder

from . import __version__
from .bot import StammtischBot
from .config import Config, ConfigError
from .mqtt_ha import HomeAssistantMqtt
from .sheets import Sheets
from .store import Store

log = logging.getLogger(__name__)
RETRY_S = 60
CONFLICT_WINDOW_S = 120   # so lange nach dem letzten Konflikt gilt er als aktuell
CONFLICT_HINT = ("Ein anderes Programm ruft mit demselben Bot-Token Nachrichten ab (z. B. Node-RED). "
                 "Der Bot bekommt dann keine Befehle und Stimmen mehr. Das andere Programm stoppen "
                 "oder bei @BotFather mit /revoke einen neuen Token erzeugen.")


class BotRunner:
    def __init__(self, store: Store):
        self.store = store
        self.state = "gestoppt"       # gestoppt | nicht konfiguriert | startet | läuft | Fehler
        self.error: str | None = None
        self.username: str | None = None
        self.bot: StammtischBot | None = None
        self._app: Application | None = None
        self._ha: HomeAssistantMqtt | None = None
        self._mqtt_task: asyncio.Task | None = None
        self._task: asyncio.Task | None = None
        self.conflict_since: float | None = None   # Beginn der aktuellen Konflikt-Phase
        self.conflict_last: float | None = None

    @property
    def mqtt_connected(self) -> bool:
        return bool(self._ha and self._ha.connected)

    @property
    def conflict(self) -> bool:
        """True, solange ein anderes Programm denselben Token abfragt."""
        return bool(self._app and self.conflict_last and time.time() - self.conflict_last < CONFLICT_WINDOW_S)

    def _on_polling_error(self, exc: TelegramError) -> None:
        # Ersetzt PTBs Standard-Callback, der jeden Fehler mit komplettem Traceback loggt
        if not isinstance(exc, Conflict):
            log.error("Fehler beim Abrufen der Updates: %s", exc)
            return
        now = time.time()
        if not self.conflict:
            self.conflict_since = now
            log.warning("Konflikt: %s", CONFLICT_HINT)
            if self.bot:
                asyncio.get_running_loop().create_task(self.bot.notify_admin("conflict", CONFLICT_HINT))
        self.conflict_last = now

    async def restart(self) -> None:
        await self.stop()
        self._task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self._teardown()
        self.state = "gestoppt"

    async def _supervise(self) -> None:
        while True:
            try:
                cfg = Config.from_values(self.store.settings())
            except ConfigError as e:
                self.state, self.error = "nicht konfiguriert", str(e)
                log.warning("Nicht konfiguriert: %s", e)
                return
            self.state, self.error = "startet", None
            try:
                await self._start(cfg)
                self.state = "läuft"
                await asyncio.Event().wait()   # läuft, bis stop() den Task abbricht
            except asyncio.CancelledError:
                raise
            except InvalidToken:
                self.state, self.error = "Fehler", "Bot-Token ungültig"
                await self._teardown()
                return
            except Exception as e:  # noqa: BLE001
                log.exception("Start fehlgeschlagen")
                self.state, self.error = "Fehler", f"{type(e).__name__}: {e} – neuer Versuch in {RETRY_S} s"
                await self._teardown()
                await asyncio.sleep(RETRY_S)

    async def _start(self, cfg: Config) -> None:
        if cfg.legacy_message_id and self.store.get("message_id") is None:
            self.store.set(message_id=cfg.legacy_message_id, poll_id=cfg.legacy_poll_id, active="0")
            log.info("Alte Node-RED-Umfrage %s übernommen", cfg.legacy_message_id)

        app = ApplicationBuilder().token(cfg.telegram_token).rate_limiter(AIORateLimiter()).build()
        ha = HomeAssistantMqtt(cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_user, cfg.mqtt_password,
                               cfg.mqtt_base, cfg.discovery_prefix)
        bot = StammtischBot(cfg, app, self.store, Sheets(cfg.google_credentials, cfg.sheet_id, cfg.sheet_tab), ha)
        bot.register()
        self._app, self._ha, self.bot = app, ha, bot

        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, error_callback=self._on_polling_error)
        self.username = app.bot.username
        log.info("Stammtisch-Bot %s läuft als @%s", __version__, self.username)
        self._mqtt_task = asyncio.create_task(ha.run())
        await bot.publish()
        await bot._run_job("Nachholen", bot.catch_up)

    async def _teardown(self) -> None:
        if self._ha:
            await self._ha.close()
        if self._mqtt_task:
            self._mqtt_task.cancel()
            self._mqtt_task = None
        app, self._app = self._app, None
        if app:
            for step in (lambda: app.updater.stop() if app.updater and app.updater.running else None,
                         lambda: app.stop() if app.running else None,
                         app.shutdown):
                try:
                    result = step()
                    if result is not None:
                        await result
                except Exception:  # noqa: BLE001
                    log.debug("Fehler beim Beenden", exc_info=True)
        self._ha, self.bot, self.username = None, None, None
        self.conflict_since = self.conflict_last = None
