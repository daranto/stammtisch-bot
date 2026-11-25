import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from stammtisch_bot.config import Config, ConfigError, validate
from stammtisch_bot.store import Store
from stammtisch_bot.web import check_password, create_app, hash_password

KEY = json.dumps({"client_email": "bot@x.iam.gserviceaccount.com", "private_key": "-----BEGIN…"})
GOOD = {"telegram_token": "1:abc", "chat_id": "-100", "mqtt_host": "mqtt.local"}


class FakeRunner:
    state, error, username, bot, mqtt_connected = "nicht konfiguriert", None, None, None, False
    restarts = 0

    async def restart(self):
        FakeRunner.restarts += 1


def test_config_from_values():
    cfg = Config.from_values({**GOOD, "google_credentials": KEY})
    assert cfg.chat_id == -100 and cfg.mqtt_port == 1883 and cfg.poll_start.weekday == 1
    assert cfg.google_credentials["client_email"].startswith("bot@")
    with pytest.raises(ConfigError):
        Config.from_values({"chat_id": "abc"})
    assert any("Bot-Token" in e for e in validate({**GOOD, "telegram_token": ""}))
    assert any("JSON" in e for e in validate({**GOOD, "google_credentials": "{kaputt"}))
    assert any("Zeitzone" in e for e in validate({**GOOD, "tz": "Mars/Base"}))


def test_password_hash():
    h = hash_password("geheim123")
    assert check_password("geheim123", h) and not check_password("falsch", h)


async def test_web_flow():
    store = Store(":memory:")
    client = TestClient(TestServer(create_app(store, FakeRunner())))
    await client.start_server()
    try:
        r = await client.get("/", allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/setup"

        r = await client.post("/setup", data={"pw": "geheim123", "pw2": "geheim123"}, allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/settings"
        assert "stammtisch_session" in r.cookies

        r = await client.get("/settings")
        assert r.status == 200 and "Bot-Token" in await r.text()

        # unvollständig -> Fehler, nichts gespeichert
        r = await client.post("/settings", data={"telegram_token": "1:abc"}, allow_redirects=False)
        assert "err=" in r.headers["Location"] and store.settings() == {}

        data = {**GOOD, "google_credentials": KEY, "poll_start_day": "do", "poll_start_time": "08:30",
                "poll_end_day": "fr", "poll_end_time": "18:00",
                "tz": "Europe/Berlin", "mqtt_port": "1883", "mqtt_base": "stammtisch",
                "discovery_prefix": "homeassistant"}
        r = await client.post("/settings", data=data, allow_redirects=False)
        assert r.headers["Location"].startswith("/?ok=") and FakeRunner.restarts == 1
        assert store.settings()["telegram_token"] == "1:abc"
        assert store.settings()["poll_start"] == "do 08:30" and store.settings()["poll_end"] == "fr 18:00"

        # Secret leer lassen = unverändert, Token erscheint nicht im HTML
        r = await client.post("/settings", data={**data, "telegram_token": "", "google_credentials": ""},
                              allow_redirects=False)
        assert store.settings()["telegram_token"] == "1:abc" and "client_email" in store.settings()["google_credentials"]
        html = await (await client.get("/settings")).text()
        assert "1:abc" not in html and "bot@x.iam.gserviceaccount.com" in html
        assert '<option value="do" selected>Donnerstag</option>' in html
        assert 'name="poll_start_time" value="08:30"' in html
        assert "<option selected>Europe/Berlin</option>" in html

        status = await (await client.get("/")).text()
        assert "nicht konfiguriert" in status

        await client.post("/logout")
        client.session.cookie_jar.clear()
        r = await client.get("/", allow_redirects=False)
        assert r.headers["Location"] == "/login"
        r = await client.post("/login", data={"pw": "geheim123"}, allow_redirects=False)
        assert r.headers["Location"] == "/"
    finally:
        await client.close()


async def test_checks_without_settings_and_schedule():
    from stammtisch_bot.checks import check_schedule, run_checks

    store = Store(":memory:")
    res = await run_checks(store, False)
    assert res[0].name == "Einstellungen" and res[0].status == "fehler"

    cfg = Config.from_values(GOOD)
    (c,) = check_schedule(cfg, store)
    assert c.status in ("ok", "hinweis") and ("nächste Umfrage" in c.detail or "verpasst" in c.detail)
    store.set(active="1", kw=48, ends_at="2026-11-25T15:00:00+01:00")
    (c,) = check_schedule(cfg, store)
    assert c.detail == "Umfrage KW 48 läuft · endet Mi 25.11. 15:00"
