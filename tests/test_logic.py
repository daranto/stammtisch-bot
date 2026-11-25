from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from stammtisch_bot import texts
from stammtisch_bot.config import WeeklyTime
from stammtisch_bot.mqtt_ha import discovery_payload
from stammtisch_bot.schedule import calendar_week, cycle, end_after
from stammtisch_bot.sheets import KEIN_STANDORT, fmt_de, last_amount, parse_euro, standort_for_week, sum_liters
from stammtisch_bot.store import Store

TZ = ZoneInfo("Europe/Berlin")
START, END = WeeklyTime.parse("di 12:00"), WeeklyTime.parse("mi 15:00")


def test_weekly_time_parse():
    assert WeeklyTime.parse("Mi 15:00") == WeeklyTime(2, time(15, 0))
    assert WeeklyTime.parse("tue 08:30") == WeeklyTime(1, time(8, 30))
    with pytest.raises(ValueError):
        WeeklyTime.parse("dienstag")


def test_cycle_and_end():
    now = datetime(2026, 11, 25, 10, 0, tzinfo=TZ)  # Mittwoch
    s, e = cycle(now, START, END)
    assert s == datetime(2026, 11, 24, 12, 0, tzinfo=TZ)
    assert e == datetime(2026, 11, 25, 15, 0, tzinfo=TZ)
    # regulärer Start -> Ende Mittwoch
    assert end_after(s, START, END) == e
    # manueller Start am Donnerstag -> Ende nächste Woche Mittwoch
    assert end_after(datetime(2026, 11, 26, 9, 0, tzinfo=TZ), START, END) == datetime(2026, 12, 2, 15, 0, tzinfo=TZ)
    # manueller Start am Montag -> Ende diese Woche
    assert end_after(datetime(2026, 11, 23, 9, 0, tzinfo=TZ), START, END) == e


def test_calendar_week():
    assert calendar_week(datetime(2026, 11, 24, tzinfo=TZ)) == 48
    assert calendar_week(datetime(2027, 1, 1, tzinfo=TZ)) == 53


@pytest.mark.parametrize("n,expected", [(0, None), (1, "5l oder Kasten"), (3, "5l oder Kasten"),
                                        (4, "10l"), (12, "20l"), (13, "30l+")])
def test_biermenge(n, expected):
    assert texts.biermenge(n) == expected


def test_verlauf():
    assert texts.verlauf(0, 2, None).endswith(texts.AUSFALL)
    assert "Austragungsort: Kalle" in texts.verlauf(5, 1, "Kalle")


def test_zu_spaet_kategorie():
    assert texts.zu_spaet_kategorie([]) == "zurueckgezogen"
    assert texts.zu_spaet_kategorie([0]) == "bier"
    assert texts.zu_spaet_kategorie([1]) == "keinBier"
    assert texts.zu_spaet_kategorie([2]) is None


def test_sheets_parsing():
    rows = [["Datum", "Wer"], ["19.11.2026", "Alt"], ["26.11.2026", "Kalle"], ["", ""]]
    assert standort_for_week(rows, date(2026, 11, 24)) == "Kalle"
    assert standort_for_week(rows, date(2026, 12, 1)) == KEIN_STANDORT
    assert sum_liters([["Liter"], ["20l"], ["2,5 l"], ["10L"], ["x"]]) == 32.5
    assert parse_euro("1.234,56 €") == 1234.56
    assert parse_euro("1.234") == 1234
    assert last_amount([["Kasse"], ["10"], ["1.500,00 €"], [""]]) == 1500
    assert fmt_de(32.5) == "32,5" and fmt_de(30.0) == "30" and fmt_de(1234.5, 2) == "1234,50"


def test_store_votes_and_late_calls():
    s = Store(":memory:")
    s.set(poll_id="p1", active="1", kw=48)
    assert s.poll().active and s.poll().kw == 48
    s.set_vote("p1", 1, "Anna", 0)
    s.set_vote("p1", 2, "Ben", 1)
    s.set_vote("p1", 3, "Cem", 0)
    s.set_vote("p1", 3, "Cem", 2)       # umentschieden -> Absage
    s.set_vote("p1", 2, "Ben", None)    # zurückgezogen
    s.set_vote("p0", 9, "Alt", 0)       # andere Umfrage
    c = s.counts("p1")
    assert (c.beer, c.nobeer, c.names_beer) == (1, 0, ["Anna"])
    assert s.late_call_allowed("p1", 1, 120) is True
    assert s.late_call_allowed("p1", 1, 120) is False
    s.add_history("p1", 2026, 48, "x", 1, 0, "Kalle", "Text")
    assert s.last_history_text() == "Text"


def test_discovery_payload():
    d = discovery_payload("stammtisch")
    assert d["dev"]["ids"] == ["stammtisch_bot"]
    ids = [c["unique_id"] for c in d["cmps"].values()]
    assert len(ids) == len(set(ids))
    assert all("p" in c for c in d["cmps"].values())
