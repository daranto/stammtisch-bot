"""Einstellungen: Felder für die Weboberfläche und die daraus gebaute Laufzeit-Konfiguration."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6,
            "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass(frozen=True)
class WeeklyTime:
    """Wochentag (0 = Montag) + Uhrzeit, z. B. aus "di 12:00"."""
    weekday: int
    at: time

    @classmethod
    def parse(cls, text: str) -> "WeeklyTime":
        try:
            day, hhmm = text.strip().lower().split()
            h, m = hhmm.split(":")
            return cls(WEEKDAYS[day], time(int(h), int(m)))
        except (ValueError, KeyError) as e:
            raise ValueError(f"Ungültige Zeitangabe {text!r}, erwartet z. B. 'di 12:00'") from e


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    section: str
    kind: str = "text"          # text | secret | int | json
    default: str = ""
    required: bool = False
    help: str = ""


FIELDS: list[Field] = [
    Field("telegram_token", "Bot-Token", "Telegram", "secret", required=True,
          help="Von @BotFather, z. B. 123456:ABC…"),
    Field("chat_id", "Gruppen-Chat-ID", "Telegram", "int", required=True,
          help="ID der Stammtisch-Gruppe, beginnt meist mit -"),
    Field("admin_chat_id", "Admin-Chat-ID", "Telegram", "int",
          help="Optional: Fehlermeldungen gehen an diesen Chat (z. B. deine User-ID)"),
    Field("poll_start", "Umfrage starten", "Zeitplan", default="di 12:00", required=True,
          help="Wochentag (mo–so) und Uhrzeit"),
    Field("poll_end", "Umfrage beenden", "Zeitplan", default="mi 15:00", required=True),
    Field("tz", "Zeitzone", "Zeitplan", default="Europe/Berlin", required=True),
    Field("mqtt_host", "Broker", "MQTT / Home Assistant", required=True, help="IP oder Hostname"),
    Field("mqtt_port", "Port", "MQTT / Home Assistant", "int", default="1883", required=True),
    Field("mqtt_user", "Benutzer", "MQTT / Home Assistant"),
    Field("mqtt_password", "Passwort", "MQTT / Home Assistant", "secret"),
    Field("mqtt_base", "Basis-Topic", "MQTT / Home Assistant", default="stammtisch", required=True),
    Field("discovery_prefix", "Discovery-Präfix", "MQTT / Home Assistant", default="homeassistant",
          required=True),
    Field("sheet_id", "Sheet-ID", "Google Sheet",
          help="Aus der URL: docs.google.com/spreadsheets/d/<ID>/… – für /l, /m, /bagger und den Standort"),
    Field("sheet_tab", "Tabellenblatt", "Google Sheet", default="Stammtisch-Log"),
    Field("google_credentials", "Service-Account-Key (JSON)", "Google Sheet", "json",
          help="Inhalt der JSON-Datei des Service-Accounts einfügen. Das Sheet muss für dessen E-Mail freigegeben sein."),
    Field("bagger_ziel", "Bagger-Ziel (€)", "Google Sheet", "int", default="5500"),
    Field("openai_api_key", "API-Key", "ChatGPT (/bier)", "secret", help="Leer lassen = /bier ist aus"),
    Field("openai_model", "Modell", "ChatGPT (/bier)", default="gpt-4o-mini"),
    Field("legacy_message_id", "Message-ID der alten Umfrage", "Umstieg von Node-RED", "int",
          help="Nur beim ersten Start: diese Umfrage wird beim nächsten Umfragestart geschlossen"),
    Field("legacy_poll_id", "Telegram-Poll-ID der alten Umfrage", "Umstieg von Node-RED",
          help="Damit der Zu-spät-Caller für die alte Umfrage weiterläuft"),
]
FIELD_BY_KEY = {f.key: f for f in FIELDS}


def validate(values: dict[str, str]) -> list[str]:
    """Fehlermeldungen für die Weboberfläche (leere Liste = alles gut)."""
    errors = []
    for f in FIELDS:
        v = (values.get(f.key) or "").strip()
        if not v:
            if f.required:
                errors.append(f"{f.section} › {f.label}: fehlt")
            continue
        if f.kind == "int":
            try:
                int(v)
            except ValueError:
                errors.append(f"{f.section} › {f.label}: keine Zahl")
        elif f.kind == "json":
            try:
                data = json.loads(v)
                if not isinstance(data, dict) or "client_email" not in data or "private_key" not in data:
                    errors.append(f"{f.label}: kein Service-Account-Key (client_email/private_key fehlt)")
            except ValueError:
                errors.append(f"{f.label}: kein gültiges JSON")
    for key in ("poll_start", "poll_end"):
        if values.get(key):
            try:
                WeeklyTime.parse(values[key])
            except ValueError as e:
                errors.append(f"{FIELD_BY_KEY[key].label}: {e}")
    if values.get("tz"):
        try:
            ZoneInfo(values["tz"])
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"Zeitzone {values['tz']!r} unbekannt")
    return errors


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    telegram_token: str
    chat_id: int
    admin_chat_id: int | None
    tz: ZoneInfo
    poll_start: WeeklyTime
    poll_end: WeeklyTime

    mqtt_host: str
    mqtt_port: int
    mqtt_user: str | None
    mqtt_password: str | None
    mqtt_base: str
    discovery_prefix: str

    google_credentials: dict[str, Any] | None
    sheet_id: str | None
    sheet_tab: str
    bagger_ziel: float

    openai_api_key: str | None
    openai_model: str

    legacy_message_id: int | None
    legacy_poll_id: str | None

    @classmethod
    def from_values(cls, raw: dict[str, str]) -> "Config":
        values = {f.key: (raw.get(f.key) or f.default or "").strip() for f in FIELDS}
        errors = validate(values)
        if errors:
            raise ConfigError("; ".join(errors))
        opt = lambda k: values[k] or None  # noqa: E731
        num = lambda k: int(values[k]) if values[k] else None  # noqa: E731
        return cls(
            telegram_token=values["telegram_token"],
            chat_id=int(values["chat_id"]),
            admin_chat_id=num("admin_chat_id"),
            tz=ZoneInfo(values["tz"]),
            poll_start=WeeklyTime.parse(values["poll_start"]),
            poll_end=WeeklyTime.parse(values["poll_end"]),
            mqtt_host=values["mqtt_host"],
            mqtt_port=int(values["mqtt_port"]),
            mqtt_user=opt("mqtt_user"),
            mqtt_password=opt("mqtt_password"),
            mqtt_base=values["mqtt_base"],
            discovery_prefix=values["discovery_prefix"],
            google_credentials=json.loads(values["google_credentials"]) if values["google_credentials"] else None,
            sheet_id=opt("sheet_id"),
            sheet_tab=values["sheet_tab"] or "Stammtisch-Log",
            bagger_ziel=float(values["bagger_ziel"] or 5500),
            openai_api_key=opt("openai_api_key"),
            openai_model=values["openai_model"] or "gpt-4o-mini",
            legacy_message_id=num("legacy_message_id"),
            legacy_poll_id=opt("legacy_poll_id"),
        )
