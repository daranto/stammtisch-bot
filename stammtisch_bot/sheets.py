"""Google Sheet (Stammtisch-Log): Standort, Liter, Kasse."""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta

KEIN_STANDORT = "Aktuell noch kein Standort vorhanden"


# --- reine Auswertung (testbar) ----------------------------------------------

def _parse_german_date(s: str) -> date | None:
    try:
        d, m, y = str(s).strip().split(".")
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def standort_for_week(rows: list[list[str]], today: date) -> str:
    """Eintrag der Woche (Mo–So) von `today` aus den Spalten B:C, erste Zeile = Kopf."""
    data = [r for r in rows if len(r) > 1 and r[0]][1:]
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    result = KEIN_STANDORT
    for r in data:
        d = _parse_german_date(r[0])
        if d and monday <= d <= sunday:
            result = r[1]
    return result


_LITER = re.compile(r"(\d+(?:[.,]\d+)?)\s*l", re.IGNORECASE)


def sum_liters(rows: list[list[str]]) -> float:
    total = 0.0
    for r in rows:
        if r:
            m = _LITER.search(str(r[0]))
            if m:
                total += float(m.group(1).replace(",", "."))
    return round(total, 1)


def parse_euro(v: object) -> float:
    """'1.234,56 €' -> 1234.56"""
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return float(s)


def last_amount(rows: list[list[str]]) -> float:
    values = [r[0] for r in rows if r and str(r[0]).strip()]
    if not values:
        raise ValueError("Spalte ist leer")
    return parse_euro(values[-1])


def fmt_de(x: float, decimals: int = 1) -> str:
    s = f"{x:.{decimals}f}".replace(".", ",")
    return s[:-2] if decimals == 1 and s.endswith(",0") else s


# --- Zugriff ---------------------------------------------------------------------

class Sheets:
    def __init__(self, credentials: dict | None, sheet_id: str | None, tab: str):
        self.credentials = credentials
        self.sheet_id = sheet_id
        self.tab = tab
        self._ws = None

    @property
    def enabled(self) -> bool:
        return bool(self.credentials and self.sheet_id)

    @property
    def url(self) -> str | None:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit" if self.sheet_id else None

    def _get(self, rng: str) -> list[list[str]]:
        import gspread  # erst hier laden, Start bleibt schnell

        if self._ws is None:
            gc = gspread.service_account_from_dict(self.credentials)
            self._ws = gc.open_by_key(self.sheet_id).worksheet(self.tab)
        return self._ws.get(rng)

    async def get(self, rng: str) -> list[list[str]]:
        if not self.enabled:
            raise RuntimeError("Google Sheet ist nicht konfiguriert")
        return await asyncio.wait_for(asyncio.to_thread(self._get, rng), timeout=30)

    async def standort(self, now: datetime) -> str:
        return standort_for_week(await self.get("B:C"), now.date())

    async def liters(self) -> float:
        return sum_liters(await self.get("D:D"))

    async def kasse(self) -> float:
        return last_amount(await self.get("F:F"))
