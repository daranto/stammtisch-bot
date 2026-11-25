"""Home Assistant über MQTT: Device-Discovery, Zustand, Buttons/Schalter."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiomqtt

from . import __version__

log = logging.getLogger(__name__)

CommandHandler = Callable[[str, str], Awaitable[None]]


def discovery_payload(base: str) -> dict[str, Any]:
    """Ein Gerät mit allen Entitäten (HA-Device-Discovery, ab HA 2024.11)."""
    state = f"{base}/state"
    avail = f"{base}/availability"

    def sensor(key: str, name: str, icon: str, **extra: Any) -> dict[str, Any]:
        return {"p": "sensor", "name": name, "unique_id": f"stammtisch_{key}", "icon": icon,
                "state_topic": state, "value_template": f"{{{{ value_json.{key} }}}}", **extra}

    def button(key: str, name: str, icon: str) -> dict[str, Any]:
        return {"p": "button", "name": name, "unique_id": f"stammtisch_btn_{key}", "icon": icon,
                "command_topic": f"{base}/cmd/{key}", "payload_press": "PRESS"}

    count = {"state_class": "measurement", "unit_of_measurement": "Pers."}
    return {
        "dev": {"ids": ["stammtisch_bot"], "name": "Stammtisch Bot", "mf": "Eigenbau",
                "mdl": "Telegram-Bot", "sw": __version__},
        "o": {"name": "stammtisch-bot", "sw": __version__},
        "availability_topic": avail,
        "qos": 1,
        "cmps": {
            "kw": sensor("kw", "Kalenderwoche", "mdi:calendar-week"),
            "beer": sensor("beer", "Zusagen mit Bier", "mdi:beer", **count,
                           json_attributes_topic=state,
                           json_attributes_template="{{ {'namen': value_json.names_beer} | tojson }}"),
            "nobeer": sensor("nobeer", "Zusagen ohne Bier", "mdi:cup-water", **count,
                             json_attributes_topic=state,
                             json_attributes_template="{{ {'namen': value_json.names_nobeer} | tojson }}"),
            "sum": sensor("sum", "Zusagen gesamt", "mdi:account-group", **count),
            "empfehlung": sensor("empfehlung", "Empfohlene Biermenge", "mdi:keg"),
            "standort": sensor("standort", "Standort", "mdi:home-map-marker"),
            "letzter_status": {
                **sensor("letzter_status", "Letzter Status", "mdi:text-box-check"),
                # State darf max. 255 Zeichen haben -> voller Text als Attribut
                "value_template": "{{ value_json.letzter_status[:250] }}",
                "json_attributes_topic": state,
                "json_attributes_template": "{{ {'text': value_json.letzter_status} | tojson }}",
            },
            "ends_at": sensor("ends_at", "Umfrage endet", "mdi:timer-sand", device_class="timestamp"),
            "active": {"p": "binary_sensor", "name": "Umfrage läuft", "unique_id": "stammtisch_active",
                       "icon": "mdi:poll", "state_topic": state, "value_template": "{{ value_json.active }}"},
            "message_id": sensor("message_id", "Message-ID", "mdi:message", entity_category="diagnostic"),
            "poll_id": sensor("poll_id", "Telegram-Poll-ID", "mdi:identifier", entity_category="diagnostic"),
            "late_caller": {"p": "switch", "name": "Zu-spät-Caller", "unique_id": "stammtisch_late_caller",
                            "icon": "mdi:bullhorn", "state_topic": state,
                            "value_template": "{{ value_json.late_caller }}",
                            "command_topic": f"{base}/cmd/late_caller", "entity_category": "config"},
            "btn_start": button("start", "Umfrage jetzt starten", "mdi:play"),
            "btn_end": button("end", "Umfrage jetzt beenden", "mdi:stop"),
            "btn_status": button("status", "Status in Gruppe senden", "mdi:send"),
        },
    }


class HomeAssistantMqtt:
    def __init__(self, host: str, port: int, user: str | None, password: str | None,
                 base: str, discovery_prefix: str):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.base = base
        self.discovery_topic = f"{discovery_prefix}/device/stammtisch_bot/config"
        self.ha_status_topic = f"{discovery_prefix}/status"
        self.avail_topic = f"{base}/availability"
        self.state_topic = f"{base}/state"
        self.on_command: CommandHandler | None = None
        self._client: aiomqtt.Client | None = None
        self._state: dict[str, Any] | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def publish_state(self, state: dict[str, Any]) -> None:
        self._state = state
        await self._send_state()

    async def _send_state(self) -> None:
        if self._client is None or self._state is None:
            return
        try:
            await self._client.publish(self.state_topic, json.dumps(self._state, ensure_ascii=False),
                                       qos=1, retain=True)
        except aiomqtt.MqttError as e:
            log.warning("MQTT-Zustand nicht gesendet: %s", e)

    async def _announce(self, client: aiomqtt.Client) -> None:
        await client.publish(self.discovery_topic, json.dumps(discovery_payload(self.base), ensure_ascii=False),
                             qos=1, retain=True)
        await client.publish(self.avail_topic, "online", qos=1, retain=True)
        await self._send_state()

    async def run(self) -> None:
        """Verbindet sich und hält die Verbindung (mit Reconnect) bis zum Abbruch."""
        will = aiomqtt.Will(self.avail_topic, "offline", qos=1, retain=True)
        while True:
            try:
                async with aiomqtt.Client(self.host, self.port, username=self.user, password=self.password,
                                          identifier="stammtisch-bot", will=will) as client:
                    self._client = client
                    log.info("MQTT verbunden mit %s:%s", self.host, self.port)
                    await client.subscribe(f"{self.base}/cmd/#", qos=1)
                    await client.subscribe(self.ha_status_topic, qos=1)
                    await self._announce(client)
                    async for message in client.messages:
                        await self._handle(client, str(message.topic), message.payload)
            except aiomqtt.MqttError as e:
                log.warning("MQTT getrennt (%s), neuer Versuch in 10 s", e)
            finally:
                self._client = None
            await asyncio.sleep(10)

    async def _handle(self, client: aiomqtt.Client, topic: str, raw: Any) -> None:
        payload = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw or "")
        if topic == self.ha_status_topic:
            if payload == "online":  # HA neu gestartet -> alles erneut melden
                await self._announce(client)
            return
        name = topic.removeprefix(f"{self.base}/cmd/")
        if self.on_command:
            # nicht blockieren: Befehle können Telegram-Nachrichten senden
            task = asyncio.create_task(self.on_command(name, payload))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.publish(self.avail_topic, "offline", qos=1, retain=True)
            except aiomqtt.MqttError:
                pass
