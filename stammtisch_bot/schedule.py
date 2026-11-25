"""Wann läuft die Umfrage? Reine Funktionen, damit sie testbar sind."""
from __future__ import annotations

from datetime import datetime, timedelta

from .config import WeeklyTime


def _in_week(monday: datetime, wt: WeeklyTime) -> datetime:
    day = monday + timedelta(days=wt.weekday)
    return day.replace(hour=wt.at.hour, minute=wt.at.minute, second=0, microsecond=0)


def cycle(now: datetime, start: WeeklyTime, end: WeeklyTime) -> tuple[datetime, datetime]:
    """Start und Ende der Umfrage in der Woche von `now` (now muss tz-aware sein)."""
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    s = _in_week(monday, start)
    e = _in_week(monday, end)
    if e <= s:
        e += timedelta(days=7)
    return s, e


def end_after(started: datetime, start: WeeklyTime, end: WeeklyTime) -> datetime:
    """Ende der Umfrage, die zum Zeitpunkt `started` begonnen hat."""
    s, e = cycle(started, start, end)
    if started < s:  # außerplanmäßig vor dem regulären Start begonnen
        return e
    while e <= started:
        e += timedelta(days=7)
    return e


def calendar_week(now: datetime) -> int:
    """ISO-Kalenderwoche (entspricht moment.js 'w' mit Locale de)."""
    return now.isocalendar()[1]
