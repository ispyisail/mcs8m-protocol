#!/usr/bin/env bash
# install-pi.sh - set up a fresh Raspberry Pi (or any Debian/Ubuntu machine) as an
# MCS-8M controller: Node-RED + the add-ons the dashboard needs + serial access
# + this repo. Safe to run again: steps that are already done are skipped.
#
#   bash tools/install-pi.sh                  # Node-RED + add-ons + repo
#   bash tools/install-pi.sh --dashboard      # ...and load the ready-made dashboard
#   bash tools/install-pi.sh --restore DIR    # ...and restore your own backup from DIR
#   bash tools/install-pi.sh --dry-run ...    # only print what would be done
#
# See docs/08-pi-setup.md.
set -euo pipefail

REPO_URL="https://github.com/ispyisail/mcs8m-protocol.git"
REPO_DIR="$HOME/mcs8m-protocol"
NR_DIR="$HOME/.node-red"
ADDONS="node-red-dashboard node-red-contrib-ui-led node-red-node-serialport"

DRY=0; DASHBOARD=0; RESTORE=""
ME="$(id -un 2>/dev/null)" || ME="${USER:-$(id -u)}"
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)   DRY=1 ;;
        --dashboard) DASHBOARD=1 ;;
        --restore)   RESTORE="${2:?--restore needs a folder}"; shift ;;
        -h|--help)   sed -n '2,12p' "$0"; exit 0 ;;
        *)           echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then echo "   would run: $*"; else "$@"; fi; }
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(id -u)" = 0 ] && { echo "Run this as your normal user (not root/sudo); it asks for sudo itself." >&2; exit 1; }
if [ -n "$RESTORE" ]; then
    RESTORE="$(cd "$RESTORE" && pwd)" || { echo "restore folder not found" >&2; exit 1; }
    [ -f "$RESTORE/node-red/flows.json" ] || { echo "$RESTORE doesn't look like a venue backup (no node-red/flows.json)" >&2; exit 1; }
fi

say "1/7 System packages (git, python3, curl)"
run sudo apt-get update -q
run sudo apt-get install -y -q git python3 curl ca-certificates

say "2/7 Node-RED"
if have node-red; then
    echo "   already installed: $(node-red --version 2>/dev/null | head -1 || echo yes)"
else
    echo "   using the official Node-RED installer (also adds node-red-start/stop/restart and the service)"
    if [ "$DRY" = 1 ]; then
        echo "   would run: bash <(curl -sL .../update-nodejs-and-nodered-deb) --confirm-install --confirm-pi --node20"
    else
        bash <(curl -sL https://github.com/node-red/linux-installers/releases/latest/download/update-nodejs-and-nodered-deb) \
            --confirm-install --confirm-pi --node20
    fi
fi

say "3/7 Serial port permission (dialout group)"
if id -nG "$ME" | grep -qw dialout; then echo "   $ME is already in dialout"
else run sudo usermod -aG dialout "$ME"; echo "   added - takes effect after a reboot"; fi

say "4/7 This repo -> $REPO_DIR"
if [ -d "$REPO_DIR/.git" ]; then run git -C "$REPO_DIR" pull --ff-only
else run git clone "$REPO_URL" "$REPO_DIR"; fi

say "5/7 Node-RED settings and add-ons"
run mkdir -p "$NR_DIR"
if [ -n "$RESTORE" ]; then
    # your own package.json lists every add-on you had installed
    for f in package.json package-lock.json settings.js flows_cred.json .config.users.json; do
        [ -f "$RESTORE/node-red/$f" ] && run cp -p "$RESTORE/node-red/$f" "$NR_DIR/$f"
    done
    run bash -c "cd '$NR_DIR' && npm install --omit=dev --no-audit --no-fund"
else
    run bash -c "cd '$NR_DIR' && npm install --omit=dev --no-audit --no-fund $ADDONS"
fi

say "6/7 Flows"
if [ -n "$RESTORE" ]; then
    if [ -f "$NR_DIR/flows.json" ]; then
        run cp -p "$NR_DIR/flows.json" "$NR_DIR/flows.json.before-restore-$(date +%Y%m%d-%H%M%S)"
    fi
    run cp -p "$RESTORE/node-red/flows.json" "$NR_DIR/flows.json"
    if [ -d "$RESTORE/mcs8m-tools" ]; then
        run mkdir -p "$HOME/mcs8m-tools"
        run cp -rp "$RESTORE/mcs8m-tools/." "$HOME/mcs8m-tools/"
    fi
    echo "   restored your flow, settings and tools from $RESTORE"
elif [ "$DASHBOARD" = 1 ]; then
    if [ -s "$NR_DIR/flows.json" ]; then
        echo "   $NR_DIR/flows.json already exists - not overwriting it."
        echo "   Import $REPO_DIR/examples/node-red/dashboard/mcs8m-dashboard-flow.json from the editor instead."
    else
        run cp "$REPO_DIR/examples/node-red/dashboard/mcs8m-dashboard-flow.json" "$NR_DIR/flows.json"
        echo "   loaded the ready-made dashboard - edit 'EDIT SETTINGS HERE' and the serial port after starting"
    fi
else
    echo "   nothing to load (use --dashboard or --restore DIR)"
fi

say "7/7 Start Node-RED on boot"
run sudo systemctl enable nodered.service
run sudo systemctl restart nodered.service

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done"
cat <<EOF
   Editor:    http://${IP:-<this-pi>}:1880
   Dashboard: http://${IP:-<this-pi>}:1880/ui

   Next:
   - Reboot once if you were just added to 'dialout':   sudo reboot
   - Check the switcher is on Serial Cmd Group = Rem 1 (docs/07-hardware.md)
   - Check the serial port in Node-RED matches:          ls -l /dev/serial/by-id/
EOF
