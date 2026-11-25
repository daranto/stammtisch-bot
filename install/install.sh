#!/usr/bin/env bash
# Installiert oder aktualisiert den Stammtisch-Bot IM Container (Debian 12/13).
# Aufruf: vom Proxmox-Script, im Container mit "update" oder über die Weboberfläche (Seite „Update“).
set -euo pipefail

# Alles steckt in main(): bash liest die Funktion komplett ein, bevor sie läuft – so stört es nicht,
# wenn git diese Datei während des Updates austauscht.
main() {
  local REPO="${STAMMTISCH_REPO:-https://github.com/daranto/stammtisch-bot.git}"
  local BRANCH="${STAMMTISCH_BRANCH:-main}"
  local APP=/opt/stammtisch-bot
  local DATA=/var/lib/stammtisch-bot
  info() { echo -e "\e[1;34m›\e[0m $*"; }
  ok()   { echo -e "\e[1;32m✓\e[0m $*"; }

  [[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen"; exit 1; }
  export DEBIAN_FRONTEND=noninteractive
  echo "=== $(date '+%d.%m.%Y %H:%M:%S') ==="

  if [[ "${1:-}" != "--nach-download" ]]; then
    info "Pakete installieren"
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv git ca-certificates curl >/dev/null

    if [[ -d "$APP/.git" ]]; then
      info "Neueste Version holen"
      git -C "$APP" fetch -q --depth 1 origin "$BRANCH"
      git -C "$APP" reset -q --hard FETCH_HEAD
    else
      info "Code herunterladen"
      git clone -q --depth 1 -b "$BRANCH" "$REPO" "$APP"
    fi
    # Rest mit der gerade geladenen Fassung dieses Scripts erledigen
    exec bash "$APP/install/install.sh" --nach-download
  fi

  id stammtisch >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin stammtisch
  mkdir -p "$DATA" && chown stammtisch:stammtisch "$DATA" && chmod 750 "$DATA"

  info "Python-Umgebung einrichten"
  [[ -x "$APP/venv/bin/python" ]] || python3 -m venv "$APP/venv"
  "$APP/venv/bin/pip" install -q --upgrade pip
  "$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"
  git -C "$APP" rev-parse HEAD > "$APP/.commit"

  install -m 644 "$APP/install/stammtisch-bot.service" /etc/systemd/system/stammtisch-bot.service
  install -m 644 "$APP/install/stammtisch-bot-update.service" /etc/systemd/system/stammtisch-bot-update.service
  install -m 644 "$APP/install/stammtisch-bot-update.path" /etc/systemd/system/stammtisch-bot-update.path

  cat > /usr/bin/update <<'EOF'
#!/usr/bin/env bash
exec bash /opt/stammtisch-bot/install/install.sh
EOF
  cat > /usr/bin/stammtisch-reset-password <<'EOF'
#!/usr/bin/env bash
cd /opt/stammtisch-bot && exec runuser -u stammtisch -- env STAMMTISCH_DATA=/var/lib/stammtisch-bot \
  /opt/stammtisch-bot/venv/bin/python -m stammtisch_bot reset-password
EOF
  chmod 755 /usr/bin/update /usr/bin/stammtisch-reset-password

  systemctl daemon-reload
  systemctl enable -q stammtisch-bot stammtisch-bot-update.path
  systemctl start stammtisch-bot-update.path
  systemctl restart stammtisch-bot

  local IP
  IP=$(hostname -I | awk '{print $1}')
  ok "Stammtisch-Bot $(cut -c1-7 "$APP/.commit") läuft"
  echo
  echo "   Weboberfläche:  http://${IP}:8080"
  echo "   Log:            journalctl -u stammtisch-bot -f"
  echo "   Update:         update   (oder in der Weboberfläche unter „Update“)"
  echo "   Passwort weg:   stammtisch-reset-password"
}

main "$@"
