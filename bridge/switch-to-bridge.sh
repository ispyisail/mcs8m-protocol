#!/usr/bin/env bash
# switch-to-bridge.sh - move an existing Node-RED + MCS-8M setup onto the bridge, safely.
#
#   bash bridge/switch-to-bridge.sh                 switch over
#   bash bridge/switch-to-bridge.sh --dry-run       show what would change, change nothing
#   bash bridge/switch-to-bridge.sh --undo          put everything back exactly as it was
#   bash bridge/switch-to-bridge.sh --flow FILE     add FILE instead of the standard bridge dashboard
#
# What it does:
#   1. stops Node-RED (frees the serial port) and backs up flows.json
#   2. DISABLES only the Node-RED tabs that use a serial port, keeps every other tab,
#      and ADDS the bridge version of the dashboard (re-running replaces it, no duplicates)
#   3. installs and starts the bridge as a service (bridge/install-service.sh)
#   4. starts Node-RED and checks the bridge answers
# Needs bridge/config.json (created from config.example.json on the first run).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
NR="${NODE_RED_DIR:-$HOME/.node-red}"
FLOW="$REPO/examples/node-red/dashboard/mcs8m-dashboard-bridge-flow.json"
LATEST="$NR/.pre-bridge-backup"
DRY=0; MODE=switch
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --undo)    MODE=undo ;;
        --flow)    FLOW="$(cd "$(dirname "${2:?--flow needs a file}")" && pwd)/$(basename "$2")"; shift ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then echo "   would run: $*"; else "$@"; fi; }
nr()  { if command -v "node-red-$1" >/dev/null; then run "node-red-$1"; else run sudo systemctl "$1" nodered; fi; }

# ------------------------------------------------------------------ undo
if [ "$MODE" = undo ]; then
    [ -f "$LATEST" ] || { echo "No backup recorded ($LATEST) - nothing to undo." >&2; exit 1; }
    BACKUP="$(cat "$LATEST")"; [ -f "$BACKUP" ] || { echo "Backup $BACKUP is missing." >&2; exit 1; }
    say "Removing the bridge service";  run bash "$HERE/install-service.sh" --remove
    say "Restoring $BACKUP";           nr stop; run cp -p "$BACKUP" "$NR/flows.json"; nr start
    [ "$DRY" = 1 ] || rm -f "$LATEST"
    echo; echo "Back to the original setup."
    exit 0
fi

# ------------------------------------------------------------------ checks
say "Checks"
[ -f "$NR/flows.json" ] || { echo "No $NR/flows.json (set NODE_RED_DIR if Node-RED lives elsewhere)" >&2; exit 1; }
[ -f "$FLOW" ] || { echo "Flow to add not found: $FLOW" >&2; exit 1; }
if [ ! -f "$HERE/config.json" ]; then
    cp "$HERE/config.example.json" "$HERE/config.json"
    echo "Created bridge/config.json from the example. Edit it (serial port, input names), then run this again."
    exit 1
fi
PLAN="$(mktemp)"; trap 'rm -f "$PLAN" "$PLAN.json"' EXIT
python3 - "$NR/flows.json" "$FLOW" "$HERE/config.json" "$PLAN.json" > "$PLAN" <<'PY'
import json, sys
cur, add, cfg = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:4])
cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
add_ids = {n["id"] for n in add}
prefix = add[0]["id"].rsplit(".", 1)[0] + "." if "." in add[0]["id"] else None
ports = {n["id"]: n.get("serialport") for n in cur if n.get("type") == "serial-port"}
uses = {n.get("z") for n in cur if n.get("type") in ("serial in", "serial out", "serial request", "serial control")
        and n.get("serial") in ports}
tabs = {n["id"]: n for n in cur if n.get("type") == "tab"}
old = [n for n in cur if n["id"] in add_ids or (prefix and n["id"].startswith(prefix))]
keep = [n for n in cur if n not in old]
for n in keep:
    if n.get("type") == "tab" and n["id"] in uses and not n.get("disabled"):
        n["disabled"] = True
        print(f"   disable tab: {n.get('label')!r} (uses serial port {', '.join(sorted(set(filter(None, ports.values()))))})")
for t in tabs.values():
    if t["id"] not in uses and t["id"] not in add_ids:
        print(f"   keep tab:    {t.get('label')!r}")
disabled = {n["id"] for n in keep if n.get("type") == "tab" and n.get("disabled")}
widgets = [n for n in keep if n.get("group") and n.get("z")]
for g in [n for n in keep if n.get("type") == "ui_group"]:
    mine = [w for w in widgets if w["group"] == g["id"]]
    g["_only_disabled"] = bool(mine) and all(w["z"] in disabled for w in mine)
for t in [n for n in keep if n.get("type") == "ui_tab"]:
    gs = [g for g in keep if g.get("type") == "ui_group" and g.get("tab") == t["id"]]
    used = [g for g in gs if any(w["group"] == g["id"] for w in widgets)]
    if used and all(g["_only_disabled"] for g in used) and not t.get("hidden"):
        t["hidden"] = True
        print(f"   hide dashboard page: {t.get('name')!r} (its widgets are all in disabled tabs)")
for g in keep:
    g.pop("_only_disabled", None)
if old:
    print(f"   replace the bridge dashboard added earlier ({len(old)} nodes)")
have_base = any(n.get("type") == "ui_base" for n in keep)
new = [dict(n) for n in add if not (n.get("type") == "ui_base" and have_base)]
http = cfg.get("http") or {}
for n in new:                                      # point the dashboard at this bridge
    for r in n.get("rules", []) if n.get("type") == "change" else []:
        if r.get("p") == "bridge_url":
            r["to"] = f"http://127.0.0.1:{http.get('port', 8765)}"
        if r.get("p") == "bridge_token" and http.get("token"):
            r["to"] = http["token"]
print(f"   add tab:     {next((n.get('label') for n in new if n.get('type') == 'tab'), '?')!r} ({len(new)} nodes)")
if not uses:
    print("   (no tab uses a serial port - nothing to disable)")
json.dump(keep + new, open(sys.argv[4], "w", encoding="utf-8"), indent=4, ensure_ascii=False)
PY
cat "$PLAN"
PORT_DEV="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('serial_port',''))" "$HERE/config.json")"
[ -e "$PORT_DEV" ] && echo "   serial port $PORT_DEV present" || echo "   note: $PORT_DEV not found right now - the bridge will keep retrying"
echo "   TIP: close Node-RED editor tabs first, so an old tab can't re-deploy the old flow."

say "1/4 Stop Node-RED and back up flows.json"
nr stop
BACKUP="$NR/flows.json.pre-bridge-$(date +%Y%m%d-%H%M%S)"
run cp -p "$NR/flows.json" "$BACKUP"
# undo always means "back to before the bridge": keep the FIRST backup until an undo has happened
if [ "$DRY" = 0 ] && [ ! -f "$LATEST" ]; then echo "$BACKUP" > "$LATEST"; fi
echo "   backup: $BACKUP"
[ -f "$LATEST" ] && echo "   --undo will restore: $(cat "$LATEST")"

say "2/4 Update the flows (changes listed above)"
run cp "$PLAN.json" "$NR/flows.json"

say "3/4 Install and start the bridge service"
run bash "$HERE/install-service.sh"

say "4/4 Start Node-RED"
nr start
[ "$DRY" = 1 ] && { echo; echo "Dry run - nothing was changed."; exit 0; }

say "Check"
PORT="$(python3 -c "import json,sys; print((json.load(open(sys.argv[1])).get('http') or {}).get('port', 8765))" "$HERE/config.json")"
STATE=""
for i in $(seq 1 20); do STATE="$(curl -s -m 2 "http://127.0.0.1:$PORT/api/state" || true)"; [ -n "$STATE" ] && break; sleep 1; done
echo "$STATE" | python3 -c "import json,sys
s=json.load(sys.stdin); print('   bridge running; switcher', 'CONNECTED' if s['connected'] else 'not answering yet (switched on? Rem 1?)')" 2>/dev/null \
    || echo "   bridge not answering yet - see: journalctl -u mcs8m-bridge -f"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Done.  Dashboard: http://$IP:1880/ui   Bridge: http://$IP:$PORT/   Undo: bash $HERE/switch-to-bridge.sh --undo"
