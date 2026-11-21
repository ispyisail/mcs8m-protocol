#!/usr/bin/env bash
# install-service.sh - run the MCS-8M bridge as an always-on service (systemd),
# starting at boot and restarting if it ever stops. Raspberry Pi OS / Debian / Ubuntu.
#
#   bash bridge/install-service.sh              install (or update) and start
#   bash bridge/install-service.sh --remove     stop and remove
#   bash bridge/install-service.sh --dry-run    show the service file, change nothing
#
# Logs:    journalctl -u mcs8m-bridge -f
# Restart: sudo systemctl restart mcs8m-bridge
set -euo pipefail

NAME=mcs8m-bridge
UNIT=/etc/systemd/system/$NAME.service
HERE="$(cd "$(dirname "$0")" && pwd)"
ME="$(id -un 2>/dev/null)" || ME="${USER:-$(id -u)}"
PY="$(command -v python3 || echo /usr/bin/python3)"
CONFIG="$HERE/config.json"

case "${1:-}" in
  --remove)
    sudo systemctl disable --now "$NAME" 2>/dev/null || true
    sudo rm -f "$UNIT"; sudo systemctl daemon-reload
    echo "removed $NAME"; exit 0 ;;
  --dry-run|"") ;;
  -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

[ "$(id -u)" = 0 ] && { echo "Run as your normal user (it asks for sudo); the bridge should not run as root." >&2; exit 1; }

SERVICE="[Unit]
Description=MCS-8M bridge (Sony MCS-8M serial <-> network)
After=network-online.target
Wants=network-online.target

[Service]
User=$ME
SupplementaryGroups=dialout
WorkingDirectory=$HERE
ExecStart=$PY -u $HERE/mcs8m_bridge.py -c $CONFIG
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"

if [ "${1:-}" = "--dry-run" ]; then
  echo "would write $UNIT:"; echo; echo "$SERVICE"; exit 0
fi

if [ ! -f "$CONFIG" ]; then
  echo "No $CONFIG yet - creating it from config.example.json. Edit it, then run this again."
  cp "$HERE/config.example.json" "$CONFIG"
  exit 1
fi
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$CONFIG" || { echo "config.json is not valid JSON" >&2; exit 1; }

if systemctl is-active --quiet nodered 2>/dev/null; then
  echo "NOTE: Node-RED is running. If its flow uses the serial port, stop or disable that flow:"
  echo "      the bridge and Node-RED's serial nodes can't share the port. Use the bridge"
  echo "      version of the dashboard (examples/node-red/dashboard/mcs8m-dashboard-bridge-flow.json)."
fi

echo "$SERVICE" | sudo tee "$UNIT" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now "$NAME"
sleep 2
systemctl --no-pager --lines=8 status "$NAME" || true
PORT="$(python3 -c "import json,sys; print((json.load(open(sys.argv[1])).get('http') or {}).get('port', 8765))" "$CONFIG")"
echo
echo "Control page: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/"
echo "Logs:         journalctl -u $NAME -f"
