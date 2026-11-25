"""Einstieg:
    python -m stammtisch_bot                  Weboberfläche + Bot starten
    python -m stammtisch_bot reset-password   Web-Passwort zurücksetzen (nächster Aufruf fragt neu)
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from aiohttp import web

from . import __version__
from .runner import BotRunner
from .store import Store
from .web import create_app

log = logging.getLogger("stammtisch_bot")

DATA_DIR = os.environ.get("STAMMTISCH_DATA", "/var/lib/stammtisch-bot")
PORT = int(os.environ.get("STAMMTISCH_PORT", "8080"))


async def main() -> None:
    store = Store(os.path.join(DATA_DIR, "stammtisch.db"))
    runner = BotRunner(store)

    web_runner = web.AppRunner(create_app(store, runner), access_log=None)
    await web_runner.setup()
    await web.TCPSite(web_runner, "0.0.0.0", PORT).start()
    log.info("Stammtisch-Bot %s – Weboberfläche auf Port %s", __version__, PORT)

    await runner.restart()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("Beende …")
    await runner.stop()
    await web_runner.cleanup()


def run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.argv[1:] == ["reset-password"]:
        Store(os.path.join(DATA_DIR, "stammtisch.db")).set(web_password=None, web_secret=None)
        print("Passwort zurückgesetzt – beim nächsten Aufruf der Weboberfläche neu festlegen.")
        return
    asyncio.run(main())


if __name__ == "__main__":
    run()
