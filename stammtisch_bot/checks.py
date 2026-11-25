"""Verbindungstest: prüft alles, ohne in der Gruppe etwas zu schreiben."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiomqtt
from telegram import Bot
from telegram.error import ChatMigrated, Forbidden, InvalidToken, TelegramError

from .config import Config, ConfigError
from .schedule import cycle
from .sheets import Sheets
from .store import Store

OK, FEHLER, HINWEIS, AUS = "ok", "fehler", "hinweis", "aus"
TIMEOUT_S = 20
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


@dataclass
class Check:
    name: str
    status: str      # ok | fehler | hinweis | aus
    detail: str


def _when(dt: datetime) -> str:
    return f"{WOCHENTAGE[dt.weekday()]} {dt:%d.%m. %H:%M}"


async def _guard(name: str, fn: Callable[[], Awaitable[list[Check]]]) -> list[Check]:
    try:
        return await asyncio.wait_for(fn(), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        return [Check(name, FEHLER, f"keine Antwort nach {TIMEOUT_S} s")]
    except Exception as e:  # noqa: BLE001
        return [Check(name, FEHLER, f"{type(e).__name__}: {e}")]


async def check_telegram(cfg: Config) -> list[Check]:
    results: list[Check] = []
    bot = Bot(cfg.telegram_token)
    try:
        await bot.initialize()
    except InvalidToken:
        return [Check("Telegram-Bot", FEHLER, "Bot-Token ungültig")]
    try:
        me = bot.bot
        privacy = ("Datenschutzmodus an – der Bot sieht nur Befehle und Umfrage-Antworten, das reicht"
                   if not me.can_read_all_group_messages else "liest alle Gruppennachrichten")
        results.append(Check("Telegram-Bot", OK, f"@{me.username} · {privacy}"))

        hook = await bot.get_webhook_info()
        if hook.url:
            results.append(Check("Empfang", FEHLER, f"Webhook gesetzt ({hook.url}) – Polling bekommt dann nichts. "
                                                    "Webhook entfernen oder anderes Programm prüfen"))

        try:
            chat = await bot.get_chat(cfg.chat_id)
        except ChatMigrated as e:
            return results + [Check("Gruppe", FEHLER, f"Gruppe wurde zur Supergruppe – neue Chat-ID: {e.new_chat_id}")]
        except (Forbidden, TelegramError) as e:
            return results + [Check("Gruppe", FEHLER, f"{e} – ist der Bot Mitglied und die Chat-ID richtig?")]

        member = await bot.get_chat_member(cfg.chat_id, me.id)
        title = chat.title or str(cfg.chat_id)
        if member.status in ("left", "kicked"):
            results.append(Check("Gruppe", FEHLER, f"„{title}“: Bot ist nicht (mehr) Mitglied"))
        else:
            rolle = {"administrator": "Admin", "member": "Mitglied", "restricted": "eingeschränkt",
                     "creator": "Ersteller"}.get(member.status, member.status)
            results.append(Check("Gruppe", OK, f"„{title}“ ({chat.type}) · Bot ist {rolle}"))

            # Rechte: Admins dürfen alles, sonst gelten Einschränkungen bzw. Gruppenrechte
            perms = member if member.status == "restricted" else chat.permissions
            if member.status in ("administrator", "creator") or perms is None:
                results.append(Check("Senden & Umfragen", OK, "erlaubt"))
            else:
                fehlt = [n for n, ok in (("Nachrichten", getattr(perms, "can_send_messages", True)),
                                         ("Umfragen", getattr(perms, "can_send_polls", True))) if ok is False]
                results.append(Check("Senden & Umfragen", FEHLER if fehlt else OK,
                                     ("nicht erlaubt: " + ", ".join(fehlt)) if fehlt else "erlaubt"))

        if cfg.admin_chat_id:
            try:
                admin = await bot.get_chat(cfg.admin_chat_id)
                results.append(Check("Admin-Chat", OK, admin.full_name or admin.title or str(cfg.admin_chat_id)))
            except TelegramError as e:
                results.append(Check("Admin-Chat", FEHLER, f"{e} – hast du dem Bot schon einmal /start geschrieben?"))
        else:
            results.append(Check("Admin-Chat", AUS, "nicht eingerichtet"))
    finally:
        await bot.shutdown()
    return results


async def check_mqtt(cfg: Config, bot_connected: bool) -> list[Check]:
    target = f"{cfg.mqtt_host}:{cfg.mqtt_port}"
    discovery = f"{cfg.discovery_prefix}/device/stammtisch_bot/config"
    try:
        async with aiomqtt.Client(cfg.mqtt_host, cfg.mqtt_port, username=cfg.mqtt_user, password=cfg.mqtt_password,
                                  identifier="stammtisch-bot-test", timeout=10) as client:
            await client.subscribe(discovery)
            found = False
            try:
                async with asyncio.timeout(2):
                    async for msg in client.messages:
                        found = bool(json.loads(msg.payload).get("cmps"))
                        break
            except (TimeoutError, ValueError):
                pass
    except aiomqtt.MqttError as e:
        return [Check("MQTT-Broker", FEHLER, f"{target}: {e}")]
    results = [Check("MQTT-Broker", OK, f"{target} · Anmeldung ok")]
    if found:
        results.append(Check("Home-Assistant-Gerät", OK, "Discovery veröffentlicht – Gerät „Stammtisch Bot“ in HA"))
    else:
        results.append(Check("Home-Assistant-Gerät", HINWEIS if not bot_connected else FEHLER,
                             "noch keine Discovery-Nachricht – erscheint, sobald der Bot läuft"))
    return results


async def check_sheet(cfg: Config) -> list[Check]:
    if not (cfg.sheet_id and cfg.google_credentials):
        return [Check("Google Sheet", AUS, "nicht eingerichtet – /l, /m, /bagger und Standort sind aus")]
    sheets = Sheets(cfg.google_credentials, cfg.sheet_id, cfg.sheet_tab)
    email = cfg.google_credentials.get("client_email", "?")
    try:
        standort = await sheets.standort(datetime.now(cfg.tz))
    except Exception as e:  # noqa: BLE001
        text = f"{type(e).__name__}: {e}"
        if "403" in text or "PERMISSION_DENIED" in text:
            text = f"kein Zugriff – Sheet für {email} freigeben"
        elif "WorksheetNotFound" in text:
            text = f"Tabellenblatt „{cfg.sheet_tab}“ nicht gefunden"
        elif "404" in text or "SpreadsheetNotFound" in text:
            text = "Sheet-ID nicht gefunden"
        return [Check("Google Sheet", FEHLER, text)]
    results = [Check("Google Sheet", OK, f"„{cfg.sheet_tab}“ lesbar · Standort diese Woche: {standort}")]
    try:
        liter, kasse = await sheets.liters(), await sheets.kasse()
        rest = cfg.bagger_ziel - kasse
        results.append(Check("/m und /bagger", OK, f"{liter:g} l getrunken · noch {rest:.2f} € bis zum Bagger"))
    except Exception as e:  # noqa: BLE001
        results.append(Check("/m und /bagger", FEHLER, f"Spalte D oder F nicht auswertbar: {e}"))
    return results


async def check_openai(cfg: Config) -> list[Check]:
    if not cfg.openai_api_key:
        return [Check("ChatGPT (/bier)", AUS, "kein API-Key – /bier ist aus")]
    import openai

    client = openai.AsyncOpenAI(api_key=cfg.openai_api_key, timeout=15)
    try:
        await client.models.retrieve(cfg.openai_model)   # kostet nichts
    except openai.AuthenticationError:
        return [Check("ChatGPT (/bier)", FEHLER, "API-Key ungültig")]
    except openai.NotFoundError:
        return [Check("ChatGPT (/bier)", FEHLER, f"Modell „{cfg.openai_model}“ nicht verfügbar")]
    return [Check("ChatGPT (/bier)", OK, f"Key gültig · Modell {cfg.openai_model}")]


def check_schedule(cfg: Config, store: Store) -> list[Check]:
    now = datetime.now(cfg.tz)
    p = store.poll()
    if p.active:
        ende = f" · endet {_when(datetime.fromisoformat(p.ends_at))}" if p.ends_at else ""
        return [Check("Zeitplan", OK, f"Umfrage KW {p.kw} läuft{ende}")]
    start, end = cycle(now, cfg.poll_start, cfg.poll_end)
    if start <= now < end:
        return [Check("Zeitplan", HINWEIS, "Start verpasst – der Bot holt ihn spätestens in einer Stunde nach")]
    if now >= start:
        start, end = cycle(now + timedelta(days=7), cfg.poll_start, cfg.poll_end)
    return [Check("Zeitplan", OK, f"nächste Umfrage {_when(start)} · Ende {_when(end)}")]


async def run_checks(store: Store, bot_connected: bool) -> list[Check]:
    try:
        cfg = Config.from_values(store.settings())
    except ConfigError as e:
        return [Check("Einstellungen", FEHLER, str(e))]
    results = [Check("Einstellungen", OK, "vollständig")]
    groups = await asyncio.gather(
        _guard("Telegram", lambda: check_telegram(cfg)),
        _guard("MQTT-Broker", lambda: check_mqtt(cfg, bot_connected)),
        _guard("Google Sheet", lambda: check_sheet(cfg)),
        _guard("ChatGPT (/bier)", lambda: check_openai(cfg)),
    )
    for g in groups:
        results.extend(g)
    results.extend(check_schedule(cfg, store))
    return results
