"""Update aus der Weboberfläche.

Der Bot läuft ohne Root-Rechte. Er legt nur die Datei `update-request` an; eine systemd-Path-Unit
(stammtisch-bot-update.path) bemerkt das und startet install/install.sh als root.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

APP = Path(os.environ.get("STAMMTISCH_APP", "/opt/stammtisch-bot"))
DATA = Path(os.environ.get("STAMMTISCH_DATA", "/var/lib/stammtisch-bot"))
REPO = os.environ.get("STAMMTISCH_REPO_SLUG", "daranto/stammtisch-bot")
BRANCH = os.environ.get("STAMMTISCH_BRANCH", "main")
PATH_UNIT = Path("/etc/systemd/system/stammtisch-bot-update.path")
REQUEST = DATA / "update-request"
LOG = DATA / "update.log"

_cache: dict[str, object] = {}


@dataclass
class UpdateInfo:
    supported: bool
    running: bool
    installed: str | None
    latest: str | None
    latest_message: str | None
    error: str | None
    log: str


def installed_commit() -> str | None:
    try:
        return (APP / ".commit").read_text().strip() or None
    except OSError:
        return None


async def _latest() -> tuple[str | None, str | None, str | None]:
    """(sha, Commit-Titel, Fehler) – 60 s zwischengespeichert, GitHub erlaubt 60 Anfragen/h ohne Login."""
    if time.time() - float(_cache.get("ts", 0)) < 60:
        return _cache["sha"], _cache["msg"], _cache["err"]  # type: ignore[return-value]
    sha = msg = err = None
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                             headers={"Accept": "application/vnd.github+json"}) as r:
                if r.status != 200:
                    err = f"GitHub antwortet {r.status}"
                else:
                    data = await r.json()
                    sha = data["sha"]
                    msg = data["commit"]["message"].splitlines()[0]
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    _cache.update(ts=time.time(), sha=sha, msg=msg, err=err)
    return sha, msg, err


async def _unit_active() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "stammtisch-bot-update.service",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return out.decode().strip() in ("active", "activating")
    except (OSError, asyncio.TimeoutError):
        return False


def _log_tail(lines: int = 80) -> str:
    try:
        return "\n".join(LOG.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


async def status() -> UpdateInfo:
    sha, msg, err = await _latest()
    running = REQUEST.exists() or await _unit_active()
    return UpdateInfo(PATH_UNIT.exists(), running, installed_commit(), sha, msg, err, _log_tail())


def request_update() -> None:
    if not PATH_UNIT.exists():
        raise OSError("Update-Dienst nicht installiert – einmal im Container 'update' ausführen")
    REQUEST.write_text(str(time.time()))
    _cache.clear()
