"""Zustand in SQLite – ersetzt die input_*-Helfer in Home Assistant."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .texts import OPT_BIER, OPT_KEIN_BIER

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS votes (
    poll_id TEXT NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL,
    option INTEGER NOT NULL, ts REAL NOT NULL,
    PRIMARY KEY (poll_id, user_id)
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, poll_id TEXT, year INTEGER, kw INTEGER,
    ended_at TEXT, beer INTEGER, nobeer INTEGER, standort TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS late_calls (
    poll_id TEXT NOT NULL, user_id INTEGER NOT NULL, ts REAL NOT NULL,
    PRIMARY KEY (poll_id, user_id)
);
"""


@dataclass
class PollState:
    kw: int | None
    message_id: int | None
    poll_id: str | None
    active: bool
    started_at: str | None
    ends_at: str | None


@dataclass
class Counts:
    beer: int
    nobeer: int
    names_beer: list[str]
    names_nobeer: list[str]

    @property
    def total(self) -> int:
        return self.beer + self.nobeer


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # --- key/value -------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, **values: object) -> None:
        with self.db:
            for key, value in values.items():
                if value is None:
                    self.db.execute("DELETE FROM kv WHERE key = ?", (key,))
                else:
                    self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    # --- Einstellungen (Weboberfläche) ------------------------------------
    def settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM settings")}

    def save_settings(self, values: dict[str, str]) -> None:
        with self.db:
            for key, value in values.items():
                if value:
                    self.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
                else:
                    self.db.execute("DELETE FROM settings WHERE key = ?", (key,))

    # --- Umfrage ----------------------------------------------------------
    def poll(self) -> PollState:
        kw, mid = self.get("kw"), self.get("message_id")
        return PollState(
            kw=int(kw) if kw else None,
            message_id=int(mid) if mid else None,
            poll_id=self.get("poll_id"),
            active=self.get("active") == "1",
            started_at=self.get("started_at"),
            ends_at=self.get("ends_at"),
        )

    def set_vote(self, poll_id: str, user_id: int, name: str, option: int | None) -> None:
        with self.db:
            if option is None:
                self.db.execute("DELETE FROM votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))
            else:
                self.db.execute(
                    "INSERT INTO votes (poll_id, user_id, name, option, ts) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(poll_id, user_id) DO UPDATE SET name = excluded.name, "
                    "option = excluded.option, ts = excluded.ts",
                    (poll_id, user_id, name, option, time.time()))

    def counts(self, poll_id: str | None) -> Counts:
        rows = self.db.execute("SELECT name, option FROM votes WHERE poll_id = ? ORDER BY ts",
                               (poll_id or "",)).fetchall()
        beer = [r["name"] for r in rows if r["option"] == OPT_BIER]
        nobeer = [r["name"] for r in rows if r["option"] == OPT_KEIN_BIER]
        return Counts(len(beer), len(nobeer), beer, nobeer)

    # --- Verlauf -----------------------------------------------------------
    def add_history(self, poll_id: str | None, year: int, kw: int | None, ended_at: str,
                    beer: int, nobeer: int, standort: str | None, text: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO history (poll_id, year, kw, ended_at, beer, nobeer, standort, text) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (poll_id, year, kw, ended_at, beer, nobeer, standort, text))

    def last_history_text(self) -> str | None:
        row = self.db.execute("SELECT text FROM history ORDER BY id DESC LIMIT 1").fetchone()
        return row["text"] if row else None

    # --- Zu-spät-Caller ------------------------------------------------------
    def late_call_allowed(self, poll_id: str, user_id: int, cooldown_s: float) -> bool:
        """True, wenn die Person aufgerufen werden darf; merkt sich den Aufruf."""
        now = time.time()
        row = self.db.execute("SELECT ts FROM late_calls WHERE poll_id = ? AND user_id = ?",
                              (poll_id, user_id)).fetchone()
        if row and now - row["ts"] < cooldown_s:
            return False
        with self.db:
            self.db.execute("INSERT INTO late_calls (poll_id, user_id, ts) VALUES (?, ?, ?) "
                            "ON CONFLICT(poll_id, user_id) DO UPDATE SET ts = excluded.ts",
                            (poll_id, user_id, now))
        return True
