"""Alle Texte des Bots an einem Ort."""
from __future__ import annotations

POLL_OPTIONS = ["👍 + 🍺", "👍 + 🚫🍺", "👎"]
OPT_BIER, OPT_KEIN_BIER, OPT_ABSAGE = 0, 1, 2


def poll_question(kw: int) -> str:
    return f"🟣 Wer kommt am Donnerstag zum Stammtisch (KW {kw})? 🟣"


def biermenge(n: int) -> str | None:
    if n <= 0:
        return None
    if n <= 3:
        return "5l oder Kasten"
    if n <= 6:
        return "10l"
    if n <= 12:
        return "20l"
    return "30l+"


AUSFALL = "☠️ Stammtisch fällt aus oder keiner trinkt Bier ☠️"


def bier_empfehlung(n: int) -> str:
    menge = biermenge(n)
    return f"🟡 Empfohlene Biermenge: {menge}" if menge else AUSFALL


def standort_zeile(standort: str) -> str:
    return f"🟡 Der Stammtisch wird bereitgestellt von: {standort}"


def verlauf(beer: int, nobeer: int, standort: str | None) -> str:
    """Kurztext für /s außerhalb der Umfrage (wie bisher input_text.stammtisch_letzter_status)."""
    menge = biermenge(beer)
    text = f"{beer} Zusage(n) (🍺). {nobeer} Zusage(n) (🚫🍺). "
    if menge:
        return text + f"Empfohlene Menge Bier: {menge}. Austragungsort: {standort or 'unbekannt'}"
    return text + AUSFALL


HILFE = [
    "⚪ Yo Boys folgendes ist möglich:",
    "⚪ Gebt /s für den aktuellen Status ein",
    "⚪ Die Umfrage wird jeden Dienstag um 12 Uhr automatisch gestartet",
    "⚪ Die Umfrage wird jeden Mittwoch um 15 Uhr automatisch beendet und der aktuelle Status mitgeteilt",
    "⚪ Gebt /l für die Stammtischliste ein",
    "⚪ Gebt /m für die verbrauchte Biermenge ein",
]

MUSIK = ("YT: https://youtube.com/@diebierkenner?si=EdaKhrFtIKf2RFrt / "
         "YTM: https://music.youtube.com/playlist?list=PL3lyZsT-D3l36cp6WCGYTWOXzHZy3pW0E&si=rgpw27mDuLZTIFFD "
         "Top Mukke sag ick euch")

HERBERT_PROMPT = ("Rollenspiel: Du bist ein Säufer namens Herbert und kennst dich gut mit Bier aus "
                  "Du hast locker 2 Promille momentan. Teile mir bitte in deiner Rolle eine Weisheit über Bier mit.")

KEIN_STATUS = "🔴 Umfrage wurde noch nicht gestartet oder es gibt noch keine Zusagen. Letzter Status:"

# {name} wird durch eine Telegram-Erwähnung ersetzt (pingt die Person an)
ZU_SPAET = {
    "bier": [
        "🍺 Achtung, Nachzügler! {name} hat die Umfrage erst jetzt entdeckt – vermutlich unterm Bierdeckel. Zusage mit Bier ist notiert, aber die Biermenge steht schon fest – also schnell sein beim Zapfen!",
        "🐌 Breaking News: {name} hat abgestimmt! Die Schnecke im Garten war schneller, aber hey – Durst kennt keine Deadline.",
        "🕰️ Mittwoch 15 Uhr war Schluss, {name}. Aber ich drück ein Auge zu… das andere ist seit 2 Promille sowieso zu.",
        "⏰ {name} meldet sich zu Wort – nach Umfrageschluss. Ich sag dir: Wer zu spät kommt, den bestraft das Leben… und der trinkt das warme Bier.",
        "🍻 Die Biermenge steht, und gedanklich sind wir schon beim dritten Glas – und jetzt kommt {name}. Wer zu spät kommt, trinkt den Rest!",
    ],
    "keinBier": [
        "🥤 {name} kommt doch – aber zu spät UND ohne Bier. Doppelt mutig! Ich bestell vorsichtshalber einen Kamillentee.",
        "🚫🍺 Spätzünder {name} kommt nüchtern. Wenigstens einer, der sich morgen noch an alles erinnert.",
        "⏰ {name} hat nach Umfrageschluss zugesagt – ohne Bier. Bewerbung als Fahrdienst angenommen! 🚗",
        "🧃 Zu spät und kein Bier, {name}? Ich muss mich erstmal setzen… und ein Bier für dich mit aufmachen.",
        "🐢 {name} kommt doch noch angeschlichen – mit Wasser im Gepäck. Die Bierkasse freut sich trotzdem über jede Spende.",
        "🥛 Nachmeldung von {name}: Kein Bier. Wir prüfen gerade, ob das mit der Stammtisch-Satzung vereinbar ist.",
    ],
    "zurueckgezogen": [
        "🫣 Psst, {name} hat die Stimme heimlich gelöscht. Keine Sorge, ich verrat's niemandem… außer der ganzen Gruppe.",
        "🤷 {name} weiß nicht mehr, ob ja oder nein. Kenn ich – nach dem fünften Bier geht's mir genauso.",
        "🧽 {name} hat die Stimme weggewischt wie 'nen Bierfleck. Ich hab trotzdem einen Abdruck behalten.",
        "👀 {name} dachte, nach Umfrageschluss guckt keiner mehr. Falsch gedacht – ich hab immer ein Auge drauf. Meistens sogar zwei, nur leicht verschwommen.",
    ],
}


def zu_spaet_kategorie(option_ids: list[int]) -> str | None:
    if not option_ids:
        return "zurueckgezogen"
    if option_ids[0] == OPT_BIER:
        return "bier"
    if option_ids[0] == OPT_KEIN_BIER:
        return "keinBier"
    return None  # späte Absage -> kein Aufruf
