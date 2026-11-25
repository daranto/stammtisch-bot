#!/usr/bin/env bash
# Stammtisch-Bot – LXC auf Proxmox VE anlegen und einrichten.
#
#   Auf dem Proxmox-Host (Shell als root):
#     bash -c "$(curl -fsSL https://raw.githubusercontent.com/daranto/stammtisch-bot/main/ct/stammtisch-bot.sh)"
#
#   Im Container ausgeführt aktualisiert es den Bot stattdessen.
#   Vorgaben lassen sich per Variable setzen, z. B.:  CTID=150 BRIDGE=vmbr1 VLAN=20 bash -c "$(curl …)"
set -euo pipefail

RAW="${STAMMTISCH_RAW:-https://raw.githubusercontent.com/daranto/stammtisch-bot/main}"

c_blue="\e[1;34m"; c_green="\e[1;32m"; c_red="\e[1;31m"; c_dim="\e[2m"; c_off="\e[0m"
info() { echo -e "${c_blue}›${c_off} $*"; }
ok()   { echo -e "${c_green}✓${c_off} $*"; }
die()  { echo -e "${c_red}✗ $*${c_off}" >&2; exit 1; }
ask() {  # ask VAR "Frage" default
  local __var=$1 __q=$2 __def=$3 __ans
  read -r -p "$(echo -e "  ${__q} ${c_dim}[${__def}]${c_off}: ")" __ans </dev/tty || true
  printf -v "$__var" '%s' "${__ans:-$__def}"
}

# --- im Container: nur aktualisieren --------------------------------------------
if [[ -d /opt/stammtisch-bot/.git ]] && ! command -v pct >/dev/null; then
  info "Stammtisch-Bot im Container gefunden – aktualisiere"
  exec bash /opt/stammtisch-bot/install/install.sh
fi

command -v pct >/dev/null || die "Bitte auf einem Proxmox-VE-Host ausführen."
[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

cat <<'BANNER'

   ____  _                            _   _          _
  / ___|| |_ __ _ _ __ ___  _ __ ___ | |_(_)___  ___| |__
  \___ \| __/ _` | '_ ` _ \| '_ ` _ \| __| / __|/ __| '_ \
   ___) | || (_| | | | | | | | | | | | |_| \__ \ (__| | | |
  |____/ \__\__,_|_| |_| |_|_| |_| |_|\__|_|___/\___|_| |_|  🍺 Bot

BANNER

# --- Vorgaben --------------------------------------------------------------------
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_HOSTNAME="${CT_HOSTNAME:-stammtisch-bot}"
CORES="${CORES:-1}"
RAM="${RAM:-512}"
DISK="${DISK:-4}"
BRIDGE="${BRIDGE:-vmbr0}"
VLAN="${VLAN:-}"
IP="${IP:-dhcp}"
GW="${GW:-}"
STORAGE="${STORAGE:-$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active"{print $1}' \
            | grep -x local-lvm || pvesm status -content rootdir | awk 'NR>1 && $3=="active"{print $1; exit}')}"
TSTORAGE="${TSTORAGE:-$(pvesm status -content vztmpl | awk 'NR>1 && $3=="active"{print $1; exit}')}"

echo "  Container-ID $CTID · $CT_HOSTNAME · ${CORES} CPU · ${RAM} MB RAM · ${DISK} GB auf $STORAGE"
echo "  Netz: $BRIDGE${VLAN:+ (VLAN $VLAN)} · IP $IP"
echo "  Hinweis: Der Bot braucht nur ausgehende Verbindungen (Internet + MQTT) – nicht an die WAN-Bridge hängen."
echo
ask USE_DEFAULTS "Mit diesen Einstellungen anlegen? (j = ja, n = anpassen)" "j"
if [[ ! "$USE_DEFAULTS" =~ ^[jJyY]$ ]]; then
  echo
  info "Verfügbare Bridges: $(ip -br link show type bridge | awk '{print $1}' | tr '\n' ' ')"
  info "Verfügbare Speicher: $(pvesm status -content rootdir | awk 'NR>1 && $3=="active"{print $1}' | tr '\n' ' ')"
  ask CTID "Container-ID" "$CTID"
  ask CT_HOSTNAME "Hostname" "$CT_HOSTNAME"
  ask CORES "CPU-Kerne" "$CORES"
  ask RAM "RAM in MB" "$RAM"
  ask DISK "Disk in GB" "$DISK"
  ask STORAGE "Speicher" "$STORAGE"
  ask BRIDGE "Bridge" "$BRIDGE"
  ask VLAN "VLAN-Tag (leer = keins)" "${VLAN:-}"
  ask IP "IP (dhcp oder z. B. 192.168.1.50/24)" "$IP"
  if [[ "$IP" != "dhcp" ]]; then ask GW "Gateway" "${GW:-}"; fi
fi

[[ -n "$STORAGE" && -n "$TSTORAGE" ]] || die "Kein passender Speicher gefunden (rootdir/vztmpl)."
pct status "$CTID" >/dev/null 2>&1 && die "Container-ID $CTID ist schon belegt."

# --- Template ----------------------------------------------------------------------
info "Debian-Template suchen"
pveam update >/dev/null 2>&1 || true
TEMPLATE=""
for v in 13 12; do
  TEMPLATE=$(pveam available --section system | awk '{print $2}' | grep -E "^debian-${v}-standard_.*_amd64\.tar\.(zst|gz|xz)$" | sort -V | tail -1 || true)
  [[ -n "$TEMPLATE" ]] && break
done
[[ -n "$TEMPLATE" ]] || die "Kein Debian-Template gefunden."
if ! pveam list "$TSTORAGE" | grep -F "$TEMPLATE" >/dev/null; then
  info "Lade $TEMPLATE"
  pveam download "$TSTORAGE" "$TEMPLATE" >/dev/null
fi
ok "Template $TEMPLATE"

# --- Container anlegen -----------------------------------------------------------------
NET="name=eth0,bridge=${BRIDGE},ip=${IP}"
[[ -n "$GW" ]] && NET+=",gw=${GW}"
[[ -n "$VLAN" ]] && NET+=",tag=${VLAN}"

info "Lege Container $CTID an"
pct create "$CTID" "${TSTORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_HOSTNAME" --cores "$CORES" --memory "$RAM" --swap 512 \
  --rootfs "${STORAGE}:${DISK}" --net0 "$NET" \
  --unprivileged 1 --features nesting=1 --onboot 1 --ostype debian \
  --tags stammtisch --description "Stammtisch Telegram-Bot – Weboberfläche auf Port 8080" >/dev/null
pct start "$CTID"
ok "Container läuft"

info "Warte auf Netzwerk"
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 || die "Container hat kein Internet (DNS/Gateway prüfen)."

# --- Bot installieren -------------------------------------------------------------------
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$RAW/install/install.sh" -o "$TMP"
pct push "$CTID" "$TMP" /root/stammtisch-install.sh
pct exec "$CTID" -- bash /root/stammtisch-install.sh
pct exec "$CTID" -- rm -f /root/stammtisch-install.sh

CT_IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
echo
ok "Fertig! Öffne  http://${CT_IP}:8080  und lege dort Passwort, Bot-Token, MQTT usw. fest."
echo "   Update später: im Container 'update' eingeben oder dieses Script dort erneut starten."
