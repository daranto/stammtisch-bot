#!/usr/bin/env bash
# Installiert oder aktualisiert den Stammtisch-Bot IM Container (Debian 12/13).
# Wird vom Proxmox-Script aufgerufen; im Container danach einfach "update" eingeben.
set -euo pipefail

REPO="${STAMMTISCH_REPO:-https://github.com/daranto/stammtisch-bot.git}"
BRANCH="${STAMMTISCH_BRANCH:-main}"
APP=/opt/stammtisch-bot
DATA=/var/lib/stammtisch-bot

info() { echo -e "\e[1;34m›\e[0m $*"; }
ok()   { echo -e "\e[1;32m✓\e[0m $*"; }

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

info "Pakete installieren"
apt-get update -qq
apt-get install -y -qq python3 python3-venv git ca-certificates curl >/dev/null

id stammtisch >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin stammtisch
mkdir -p "$DATA" && chown stammtisch:stammtisch "$DATA" && chmod 750 "$DATA"

if [[ -d "$APP/.git" ]]; then
  info "Neueste Version holen"
  git -C "$APP" fetch -q --depth 1 origin "$BRANCH"
  git -C "$APP" reset -q --hard FETCH_HEAD
else
  info "Code herunterladen"
  git clone -q --depth 1 -b "$BRANCH" "$REPO" "$APP"
fi

info "Python-Umgebung einrichten"
[[ -x "$APP/venv/bin/python" ]] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

install -m 644 "$APP/install/stammtisch-bot.service" /etc/systemd/system/stammtisch-bot.service
cat > /usr/bin/update <<'EOF'
#!/usr/bin/env bash
exec bash /opt/stammtisch-bot/install/install.sh
EOF
chmod 755 /usr/bin/update
cat > /usr/bin/stammtisch-reset-password <<'EOF'
#!/usr/bin/env bash
cd /opt/stammtisch-bot && exec runuser -u stammtisch -- env STAMMTISCH_DATA=/var/lib/stammtisch-bot \
  /opt/stammtisch-bot/venv/bin/python -m stammtisch_bot reset-password
EOF
chmod 755 /usr/bin/stammtisch-reset-password

systemctl daemon-reload
systemctl enable -q stammtisch-bot
systemctl restart stammtisch-bot

IP=$(hostname -I | awk '{print $1}')
ok "Stammtisch-Bot $(git -C "$APP" rev-parse --short HEAD) läuft"
echo
echo "   Weboberfläche:  http://${IP}:8080"
echo "   Log:            journalctl -u stammtisch-bot -f"
echo "   Update:         update"
echo "   Passwort weg:   stammtisch-reset-password"
