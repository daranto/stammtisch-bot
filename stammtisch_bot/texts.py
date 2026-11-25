"""Alle Texte und Regeln des Bots – in der Weboberfläche unter „Texte & Regeln“ änderbar.

Platzhalter stehen in geschweiften Klammern, z. B. {bier}. Bei mehrzeiligen Nachrichten-Feldern
wird jede Zeile als eigene Nachricht gesendet; eine Zeile entfällt, wenn ein Platzhalter darin leer ist
(z. B. die Standort-Zeile, wenn kein Standort eingetragen ist).
"""
from __future__ import annotations

import html
import re
import string
from dataclasses import dataclass
from typing import Any

OPT_BIER, OPT_KEIN_BIER, OPT_ABSAGE = 0, 1, 2


@dataclass(frozen=True)
class TextField:
    key: str
    label: str
    group: str
    kind: str          # line | multi (jede Zeile = eine Nachricht) | list (ein Eintrag pro Zeile) | number | select
    default: str
    help: str = ""
    placeholders: str = ""
    choices: tuple[tuple[str, str], ...] = ()


ERGEBNIS = "{kw} {bier} {ohne_bier} {gesamt} {menge} {empfehlung} {standort} {namen_bier} {namen_ohne_bier}"

TEXT_FIELDS: list[TextField] = [
    # --- Umfrage ---------------------------------------------------------------------------
    TextField("t_frage", "Frage", "Umfrage", "line",
              "🟣 Wer kommt am Donnerstag zum Stammtisch (KW {kw})? 🟣", placeholders="{kw}"),
    TextField("t_opt_bier", "Antwort 1 – kommt mit Bier", "Umfrage", "line", "👍 + 🍺"),
    TextField("t_opt_kein_bier", "Antwort 2 – kommt ohne Bier", "Umfrage", "line", "👍 + 🚫🍺"),
    TextField("t_opt_absage", "Antwort 3 – kommt nicht", "Umfrage", "line", "👎"),

    # --- Biermenge --------------------------------------------------------------------------
    TextField("t_menge_basis", "Berechnung nach", "Biermenge", "select", "bier",
              choices=(("bier", "Zusagen mit Bier"), ("alle", "allen Zusagen"))),
    TextField("t_menge_regeln", "Regeln", "Biermenge", "list",
              "3 = 5l oder Kasten\n6 = 10l\n12 = 20l\nsonst = 30l+",
              help="Eine Regel pro Zeile: „bis zu N Personen = Menge“. Die letzte Zeile „sonst = …“ gilt für alles darüber. "
                   "Bei 0 Personen gilt der Ausfall-Text."),
    TextField("t_empfehlung", "Empfehlung", "Biermenge", "line", "🟡 Empfohlene Biermenge: {menge}",
              placeholders="{menge}"),
    TextField("t_ausfall", "Ausfall", "Biermenge", "line", "☠️ Stammtisch fällt aus oder keiner trinkt Bier ☠️",
              help="Wird statt der Empfehlung verwendet, wenn niemand zählt"),

    # --- Nachrichten ----------------------------------------------------------------------------
    TextField("t_ende", "Umfrage beendet", "Nachrichten", "multi",
              "🟣 Stammtisch Umfrage ist beendet 🟣\n"
              "🟡 Stammtisch Zusagen (🍺): {bier}\n"
              "🟡 Stammtisch Zusagen (🚫🍺): {ohne_bier}\n"
              "{empfehlung}\n"
              "🟡 Der Stammtisch wird bereitgestellt von: {standort}",
              help="Jede Zeile = eine Nachricht", placeholders=ERGEBNIS),
    TextField("t_status", "/s während der Umfrage", "Nachrichten", "multi",
              "🟡 Stammtisch Zusage(n) (🍺): {bier}\n"
              "🟡 Stammtisch Zusage(n) (🚫🍺): {ohne_bier}\n"
              "{empfehlung}\n"
              "🟡 Der Stammtisch wird bereitgestellt von: {standort}",
              help="Jede Zeile = eine Nachricht", placeholders=ERGEBNIS),
    TextField("t_status_leer", "/s ohne laufende Umfrage", "Nachrichten", "multi",
              "🔴 Umfrage wurde noch nicht gestartet oder es gibt noch keine Zusagen. Letzter Status:\n{letzter_status}",
              help="Jede Zeile = eine Nachricht", placeholders="{letzter_status}"),
    TextField("t_verlauf", "Letzter Status (gespeichert)", "Nachrichten", "line",
              "{bier} Zusage(n) (🍺). {ohne_bier} Zusage(n) (🚫🍺). Empfohlene Menge Bier: {menge}. Austragungsort: {standort}",
              help="Wird beim Ende gespeichert und bei /s ohne Umfrage gezeigt", placeholders=ERGEBNIS),
    TextField("t_verlauf_ausfall", "Letzter Status bei Ausfall", "Nachrichten", "line",
              "{bier} Zusage(n) (🍺). {ohne_bier} Zusage(n) (🚫🍺). {ausfall}", placeholders=ERGEBNIS + " {ausfall}"),

    # --- Befehle ------------------------------------------------------------------------------------
    TextField("t_hilfe", "/h – Hilfe", "Befehle", "multi",
              "⚪ Yo Boys folgendes ist möglich:\n"
              "⚪ Gebt /s für den aktuellen Status ein\n"
              "⚪ Die Umfrage wird jeden {start_tag} um {start_zeit} Uhr automatisch gestartet\n"
              "⚪ Die Umfrage wird jeden {ende_tag} um {ende_zeit} Uhr automatisch beendet und der aktuelle Status mitgeteilt\n"
              "⚪ Gebt /l für die Stammtischliste ein\n"
              "⚪ Gebt /m für die verbrauchte Biermenge ein",
              help="Jede Zeile = eine Nachricht", placeholders="{start_tag} {start_zeit} {ende_tag} {ende_zeit}"),
    TextField("t_liste", "/l – Stammtischliste", "Befehle", "line", "{sheet_url}", placeholders="{sheet_url}"),
    TextField("t_song", "/song – Musik", "Befehle", "line",
              "YT: https://youtube.com/@diebierkenner?si=EdaKhrFtIKf2RFrt / "
              "YTM: https://music.youtube.com/playlist?list=PL3lyZsT-D3l36cp6WCGYTWOXzHZy3pW0E&si=rgpw27mDuLZTIFFD "
              "Top Mukke sag ick euch", help="Leer lassen = /song antwortet nicht"),
    TextField("t_liter", "/m – Liter", "Befehle", "line", "Es wurden schon {liter}l Bier getrunken",
              placeholders="{liter}"),
    TextField("t_bagger", "/bagger – Kasse", "Befehle", "line", "Es fehlen noch {rest} € zum Stammtisch Bagger.",
              placeholders="{rest} {kasse} {ziel}"),
    TextField("t_herbert", "/bier – Anweisung an ChatGPT", "Befehle", "line",
              "Rollenspiel: Du bist ein Säufer namens Herbert und kennst dich gut mit Bier aus Du hast locker "
              "2 Promille momentan. Teile mir bitte in deiner Rolle eine Weisheit über Bier mit."),
    TextField("t_eigene", "Eigene Befehle", "Befehle", "list", "",
              help="Ein Befehl pro Zeile: „/regeln = Antworttext“. \\n im Text = Zeilenumbruch. "
                   "Überschreibt eingebaute Befehle mit gleichem Namen."),

    # --- Nachzügler ---------------------------------------------------------------------------------
    TextField("t_spaet_cooldown", "Pause pro Person (Minuten)", "Zu-spät-Caller", "number", "2",
              help="Dieselbe Person wird höchstens so oft aufgerufen"),
    TextField("t_spaet_bier", "Spät zugesagt mit Bier", "Zu-spät-Caller", "list",
              "🍺 Achtung, Nachzügler! {name} hat die Umfrage erst jetzt entdeckt – vermutlich unterm Bierdeckel. Zusage mit Bier ist notiert, aber die Biermenge steht schon fest – also schnell sein beim Zapfen!\n"
              "🐌 Breaking News: {name} hat abgestimmt! Die Schnecke im Garten war schneller, aber hey – Durst kennt keine Deadline.\n"
              "🕰️ Mittwoch 15 Uhr war Schluss, {name}. Aber ich drück ein Auge zu… das andere ist seit 2 Promille sowieso zu.\n"
              "⏰ {name} meldet sich zu Wort – nach Umfrageschluss. Ich sag dir: Wer zu spät kommt, den bestraft das Leben… und der trinkt das warme Bier.\n"
              "🍻 Die Biermenge steht, und gedanklich sind wir schon beim dritten Glas – und jetzt kommt {name}. Wer zu spät kommt, trinkt den Rest!",
              help="Ein Text pro Zeile, zufällig gewählt. Leer = kein Aufruf", placeholders="{name}"),
    TextField("t_spaet_kein_bier", "Spät zugesagt ohne Bier", "Zu-spät-Caller", "list",
              "🥤 {name} kommt doch – aber zu spät UND ohne Bier. Doppelt mutig! Ich bestell vorsichtshalber einen Kamillentee.\n"
              "🚫🍺 Spätzünder {name} kommt nüchtern. Wenigstens einer, der sich morgen noch an alles erinnert.\n"
              "⏰ {name} hat nach Umfrageschluss zugesagt – ohne Bier. Bewerbung als Fahrdienst angenommen! 🚗\n"
              "🧃 Zu spät und kein Bier, {name}? Ich muss mich erstmal setzen… und ein Bier für dich mit aufmachen.\n"
              "🐢 {name} kommt doch noch angeschlichen – mit Wasser im Gepäck. Die Bierkasse freut sich trotzdem über jede Spende.\n"
              "🥛 Nachmeldung von {name}: Kein Bier. Wir prüfen gerade, ob das mit der Stammtisch-Satzung vereinbar ist.",
              help="Ein Text pro Zeile, zufällig gewählt. Leer = kein Aufruf", placeholders="{name}"),
    TextField("t_spaet_absage", "Spät abgesagt", "Zu-spät-Caller", "list", "",
              help="Ein Text pro Zeile. Leer = kein Aufruf (wie bisher)", placeholders="{name}"),
    TextField("t_spaet_zurueck", "Stimme zurückgezogen", "Zu-spät-Caller", "list",
              "🫣 Psst, {name} hat die Stimme heimlich gelöscht. Keine Sorge, ich verrat's niemandem… außer der ganzen Gruppe.\n"
              "🤷 {name} weiß nicht mehr, ob ja oder nein. Kenn ich – nach dem fünften Bier geht's mir genauso.\n"
              "🧽 {name} hat die Stimme weggewischt wie 'nen Bierfleck. Ich hab trotzdem einen Abdruck behalten.\n"
              "👀 {name} dachte, nach Umfrageschluss guckt keiner mehr. Falsch gedacht – ich hab immer ein Auge drauf. Meistens sogar zwei, nur leicht verschwommen.",
              help="Ein Text pro Zeile, zufällig gewählt. Leer = kein Aufruf", placeholders="{name}"),
]
TEXT_BY_KEY = {f.key: f for f in TEXT_FIELDS}
WOCHENTAG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
SPAET_KEYS = {"bier": "t_spaet_bier", "keinBier": "t_spaet_kein_bier", "absage": "t_spaet_absage",
              "zurueckgezogen": "t_spaet_zurueck"}

# Beispielwerte für Vorschau und Prüfung der Vorlagen
SAMPLE: dict[str, Any] = {
    "kw": 48, "bier": 5, "ohne_bier": 2, "gesamt": 7, "menge": "10l", "empfehlung": "🟡 Empfohlene Biermenge: 10l",
    "standort": "Kalle", "namen_bier": "Anna, Ben, Cem, Dora, Eva", "namen_ohne_bier": "Finn, Gül", "ausfall": "☠️",
    "letzter_status": "5 Zusage(n) …", "start_tag": "Dienstag", "start_zeit": "12:00", "ende_tag": "Mittwoch",
    "ende_zeit": "15:00", "sheet_url": "https://docs.google.com/…", "liter": "123,5", "rest": "1234,00",
    "kasse": "4266,00", "ziel": "5500", "name": "Anna",
}


# ------------------------------------------------------------------ Platzhalter

def _fields(template: str) -> list[str]:
    return [f for _, f, _, _ in string.Formatter().parse(template) if f]


def render(template: str, values: dict[str, Any]) -> str:
    """Ersetzt {platzhalter}; unbekannte bleiben stehen, kaputte Klammern lassen den Text unverändert."""
    class Keep(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"
    try:
        return string.Formatter().vformat(template, (), Keep(values))
    except (ValueError, IndexError, AttributeError, KeyError):
        return template


def render_lines(template: str, values: dict[str, Any]) -> list[str]:
    """Jede Zeile eine Nachricht; Zeilen mit leerem Platzhalter entfallen."""
    out = []
    for line in template.splitlines():
        if not line.strip():
            continue
        try:
            used = _fields(line)
        except ValueError:
            used = []
        if any(values.get(f) in (None, "") for f in used if f in values or f in SAMPLE):
            continue
        out.append(render(line, values))
    return out


# ------------------------------------------------------------------ Biermenge

def parse_rules(text: str) -> tuple[list[tuple[int, str]], str | None]:
    """'3 = 5l' … 'sonst = 30l+'  ->  ([(3, '5l'), …], '30l+'); wirft ValueError bei Fehlern."""
    rules: list[tuple[int, str]] = []
    fallback = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"Zeile {n}: „=“ fehlt")
        left, right = (s.strip() for s in line.split("=", 1))
        if left.lower() in ("sonst", "*", "rest", "mehr"):
            fallback = right
        else:
            m = re.fullmatch(r"(?:bis\s*)?(\d+)", left, re.IGNORECASE)
            if not m:
                raise ValueError(f"Zeile {n}: „{left}“ ist keine Zahl")
            rules.append((int(m.group(1)), right))
    rules.sort()
    if not rules and fallback is None:
        raise ValueError("mindestens eine Regel nötig")
    return rules, fallback


def parse_commands(text: str) -> dict[str, str]:
    cmds = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"Zeile {n}: „=“ fehlt")
        name, reply = (s.strip() for s in line.split("=", 1))
        name = name.lstrip("/").lower()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", name):
            raise ValueError(f"Zeile {n}: „{name}“ ist kein gültiger Befehl (a–z, 0–9, _)")
        cmds[name] = reply.replace("\\n", "\n")
    return cmds


def validate_texts(values: dict[str, str]) -> list[str]:
    errors = []
    for f in TEXT_FIELDS:
        v = values.get(f.key) or f.default
        if f.kind == "number":
            if not re.fullmatch(r"\d+(?:[.,]\d+)?", v.strip()):
                errors.append(f"{f.label}: keine Zahl")
            continue
        if f.kind == "select":
            if v not in {c for c, _ in f.choices}:
                errors.append(f"{f.label}: ungültige Auswahl")
            continue
        for line in v.splitlines() if f.kind in ("multi", "list") else [v]:
            try:
                _fields(line)
            except ValueError:
                errors.append(f"{f.label}: geschweifte Klammern stimmen nicht ({line[:40]}…)")
                break
    for key, parse in (("t_menge_regeln", parse_rules), ("t_eigene", parse_commands)):
        try:
            parse(values.get(key) or TEXT_BY_KEY[key].default)
        except ValueError as e:
            errors.append(f"{TEXT_BY_KEY[key].label}: {e}")
    for key in ("t_opt_bier", "t_opt_kein_bier", "t_opt_absage"):
        if len((values.get(key) or TEXT_BY_KEY[key].default)) > 100:
            errors.append(f"{TEXT_BY_KEY[key].label}: höchstens 100 Zeichen")
    return errors


# ------------------------------------------------------------------ Laufzeit

class Texts:
    def __init__(self, values: dict[str, str] | None = None):
        values = values or {}
        self.v = {f.key: (values.get(f.key) or f.default) for f in TEXT_FIELDS}
        self.rules, self.fallback = parse_rules(self.v["t_menge_regeln"])
        self.commands = parse_commands(self.v["t_eigene"])

    # Umfrage
    def question(self, kw: int) -> str:
        return render(self.v["t_frage"], {"kw": kw})[:300]

    @property
    def options(self) -> list[str]:
        return [self.v["t_opt_bier"], self.v["t_opt_kein_bier"], self.v["t_opt_absage"]]

    # Biermenge
    def menge(self, bier: int, ohne_bier: int = 0) -> str | None:
        n = bier + ohne_bier if self.v["t_menge_basis"] == "alle" else bier
        if n <= 0:
            return None
        for limit, amount in self.rules:
            if n <= limit:
                return amount
        return self.fallback or (self.rules[-1][1] if self.rules else None)

    def ergebnis(self, kw: int | None, bier: int, ohne_bier: int, standort: str | None,
                 namen_bier: list[str] | None = None, namen_ohne_bier: list[str] | None = None) -> dict[str, Any]:
        menge = self.menge(bier, ohne_bier)
        return {
            "kw": kw if kw is not None else "", "bier": bier, "ohne_bier": ohne_bier, "gesamt": bier + ohne_bier,
            "menge": menge or "", "standort": standort or "",
            "empfehlung": render(self.v["t_empfehlung"], {"menge": menge}) if menge else self.v["t_ausfall"],
            "ausfall": self.v["t_ausfall"],
            "namen_bier": ", ".join(namen_bier or []), "namen_ohne_bier": ", ".join(namen_ohne_bier or []),
        }

    def lines(self, key: str, values: dict[str, Any]) -> list[str]:
        return render_lines(self.v[key], values)

    def line(self, key: str, values: dict[str, Any]) -> str:
        return render(self.v[key], values)

    def verlauf(self, values: dict[str, Any]) -> str:
        key = "t_verlauf" if values.get("menge") else "t_verlauf_ausfall"
        return render(self.v[key], {**values, "standort": values.get("standort") or "unbekannt"})

    # Nachzügler
    @property
    def cooldown_s(self) -> float:
        return float(self.v["t_spaet_cooldown"].replace(",", ".")) * 60

    def late_texts(self, kategorie: str) -> list[str]:
        return [t.strip() for t in self.v[SPAET_KEYS[kategorie]].splitlines() if t.strip()]

    @staticmethod
    def late_message(text: str, user_id: int | None, name: str) -> str:
        mention = (f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>' if user_id else html.escape(name))
        return html.escape(text).replace("{name}", mention)


def zu_spaet_kategorie(option_ids: list[int]) -> str:
    if not option_ids:
        return "zurueckgezogen"
    return {OPT_BIER: "bier", OPT_KEIN_BIER: "keinBier"}.get(option_ids[0], "absage")
