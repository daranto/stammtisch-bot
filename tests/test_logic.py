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
def test_menge_standard(n, expected):
    assert texts.Texts().menge(n) == expected


def test_menge_eigene_regeln_und_basis():
    t = texts.Texts({"t_menge_regeln": "bis 2 = Kasten\n8 = 15l\nsonst = 50l", "t_menge_basis": "alle"})
    assert t.menge(1, 1) == "Kasten" and t.menge(3, 2) == "15l" and t.menge(9, 0) == "50l"
    with pytest.raises(ValueError):
        texts.parse_rules("drei = 5l")


def test_nachrichten_und_platzhalter():
    t = texts.Texts()
    vals = t.ergebnis(48, 5, 1, None)
    lines = t.lines("t_ende", vals)
    assert lines[1] == "🟡 Stammtisch Zusagen (🍺): 5" and lines[3] == "🟡 Empfohlene Biermenge: 10l"
    assert not any("bereitgestellt" in x for x in lines)          # ohne Standort entfällt die Zeile
    assert "Austragungsort: unbekannt" in t.verlauf(vals)
    assert t.verlauf(t.ergebnis(48, 0, 2, None)).endswith(t.v["t_ausfall"])
    assert t.question(48).endswith("(KW 48)? 🟣")
    assert texts.render("Hallo {name} {unbekannt}", {"name": "Bob"}) == "Hallo Bob {unbekannt}"
    assert texts.render("kaputt {", {}) == "kaputt {"


def test_eigene_befehle_und_validierung():
    t = texts.Texts({"t_eigene": "/regeln = Bitte Deckel\\nmitbringen\nPROST = 🍻"})
    assert t.commands == {"regeln": "Bitte Deckel\nmitbringen", "prost": "🍻"}
    errs = texts.validate_texts({"t_eigene": "ohne gleich", "t_frage": "KW {kw", "t_spaet_cooldown": "x"})
    assert len(errs) == 3


def test_zu_spaet():
    assert texts.zu_spaet_kategorie([]) == "zurueckgezogen"
    assert texts.zu_spaet_kategorie([0]) == "bier"
    assert texts.zu_spaet_kategorie([1]) == "keinBier"
    assert texts.zu_spaet_kategorie([2]) == "absage"
    t = texts.Texts()
    assert t.late_texts("absage") == [] and len(t.late_texts("bier")) == 5 and t.cooldown_s == 120
    assert texts.Texts.late_message("Hi {name}!", 7, "<Eva>") == 'Hi <a href="tg://user?id=7">&lt;Eva&gt;</a>!'


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
