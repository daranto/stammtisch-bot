# 🍺 Stammtisch-Bot

Telegram-Bot für die wöchentliche Stammtisch-Umfrage, mit Weboberfläche für die Einstellungen
und Home-Assistant-Anbindung über MQTT. Läuft in einem kleinen LXC-Container auf Proxmox.

## Installation (Proxmox VE)

In der Shell des Proxmox-Hosts:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/daranto/stammtisch-bot/main/ct/stammtisch-bot.sh)"
```

Das Script legt einen unprivilegierten Debian-Container an (1 CPU, 512 MB RAM, 4 GB) und installiert
den Bot. Zum Schluss zeigt es die Adresse der Weboberfläche an. Einstellungen wie Bridge, VLAN oder
eine feste IP fragt es ab, du kannst sie aber auch vorgeben:

```bash
CTID=150 BRIDGE=vmbr1 VLAN=20 bash -c "$(curl -fsSL https://raw.githubusercontent.com/daranto/stammtisch-bot/main/ct/stammtisch-bot.sh)"
```

Der Bot braucht nur **ausgehende** Verbindungen: Telegram, Google und OpenAI über HTTPS sowie den MQTT-Broker.
Hänge den Container also an die LAN- bzw. Server-Bridge, nicht an WAN.

## Einrichtung

1. `http://<container-ip>:8080` öffnen und ein Passwort festlegen.
2. Unter **Einstellungen** eintragen:
   - Telegram: Bot-Token und Gruppen-Chat-ID
   - MQTT-Broker
   - optional: Google-Sheet-ID und Service-Account-Key (JSON), OpenAI-Key für `/bier`
3. Speichern. Der Bot startet dann sofort neu, und unter **Status** siehst du, ob Telegram und MQTT verbunden sind.

Alle Einstellungen liegen in `/var/lib/stammtisch-bot/stammtisch.db`. Konfigurationsdateien oder ENV-Variablen gibt es nicht.

### Weboberfläche

| Seite | Inhalt |
|---|---|
| **Status** | Zusagen mit Namen, Zustand von Bot und MQTT, Aktionen. Dazu der **Verbindungstest**: Er prüft Bot-Token, Gruppe (Mitgliedschaft, Rechte für Nachrichten und Umfragen), Admin-Chat, MQTT samt HA-Gerät, Google Sheet, ChatGPT-Key und Zeitplan, **ohne in der Gruppe etwas zu schreiben**. Ruft ein anderes Programm (z. B. Node-RED) mit demselben Token ab, steht dort „Konflikt“ und der Admin-Chat bekommt eine Meldung. |
| **Einstellungen** | Zugangsdaten und Zeitplan |
| **Texte & Regeln** | Alle Texte des Bots, die Biermengen-Regeln (z. B. `3 = 5l oder Kasten`, `sonst = 30l+`, wahlweise nach Bier- oder allen Zusagen), die Nachzügler-Sprüche und **eigene Befehle** (`/regeln = Text`). Mit Vorschau, optional auch an den Admin-Chat. |
| **Update** | Zeigt, ob es auf GitHub eine neue Version gibt, und aktualisiert per Klick |

In den Texten stehen Platzhalter wie `{bier}`, `{standort}` oder `{namen_bier}`. Bei mehrzeiligen Feldern wird jede Zeile als eigene Nachricht gesendet. Eine Zeile entfällt, wenn ein Platzhalter darin leer ist, etwa die Standort-Zeile ohne Standort.

## Funktionen

| Was | Wann |
|---|---|
| Alte Umfrage schließen, neue senden („… (KW 48)?“) | Di 12:00 (einstellbar) |
| Stimmen pro Person zählen | solange die Umfrage läuft |
| Ergebnis, Biermenge und Standort aus dem Sheet senden | Mi 15:00 (einstellbar) |
| Zu-spät-Caller für Nachzügler | danach bis zur nächsten Umfrage |
| `/s` `/h` `/l` `/song` `/bier` `/m` `/bagger` | jederzeit, nur aus der Gruppe |

Verpasste Starts und Enden, etwa nach einem Ausfall, holt der Bot beim Start und danach stündlich nach.

## Home Assistant

Über MQTT-Discovery entsteht das Gerät **Stammtisch Bot** (ab HA 2024.11) mit diesen Entitäten:

- Sensoren: Kalenderwoche, Zusagen mit Bier, Zusagen ohne Bier (Namen jeweils als Attribut), Zusagen gesamt,
  Empfohlene Biermenge, Standort, Letzter Status, Umfrage endet
- Binärsensor: Umfrage läuft
- Schalter: Zu-spät-Caller
- Buttons: Umfrage starten, Umfrage beenden, Status in Gruppe senden
- Diagnose: Message-ID, Telegram-Poll-ID

## Wartung

Im Container gibt es dafür diese Befehle:

| Befehl | Zweck |
|---|---|
| `update` | neueste Version aus GitHub holen und neu starten (geht auch in der Weboberfläche unter „Update“) |
| `journalctl -u stammtisch-bot -f` | Log ansehen |
| `stammtisch-reset-password` | Web-Passwort vergessen: beim nächsten Aufruf neu festlegen |

Den Container sicherst du am einfachsten mit dem normalen Proxmox-Backup.

## Umstieg von Node-RED

1. Bei @BotFather einen **Test-Bot** anlegen und in einer Testgruppe ausprobieren. Pro Bot-Token darf
   immer nur ein Programm Telegram abfragen, deshalb nicht gleichzeitig mit Node-RED den echten Token nutzen.
2. Zwischen Donnerstag und Montag umstellen:
   - den Node-RED-Tab deaktivieren
   - in der Weboberfläche den echten Token und die echte Chat-ID eintragen
   - unter „Umstieg von Node-RED“ die Message-ID und die Telegram-Poll-ID der letzten Umfrage
     (`input_text.stammtisch_poll_message_id` / `input_text.stammtisch_telegram_poll_id`) eintragen
3. Das Dashboard auf die neuen Entitäten umstellen und die alten `input_*`-Helfer löschen.

## Entwicklung

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt pytest pytest-asyncio
venv/bin/pytest
STAMMTISCH_DATA=./data STAMMTISCH_PORT=8080 venv/bin/python -m stammtisch_bot
```
