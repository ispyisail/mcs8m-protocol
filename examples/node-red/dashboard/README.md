# MCS-8M dashboard for Node-RED

A ready-to-use control panel for phones and tablets. A volunteer can switch what's on the projectors without touching the switcher.

```
┌ Aux 1 ──────────────┐ ┌ Aux 2 ──────────────┐ ┌ Connection ─────────────┐
│ ● [ INPUT 1       ] │ │ ○ [ INPUT 1       ] │ │ Switcher Status: ONLINE │
│ ○ [ INPUT 2       ] │ │ ○ [ INPUT 2       ] │ │       ▄▄▄▄▄▄▄           │
│ ○ [ INPUT 3       ] │ │ ● [ INPUT 3       ] │ │       █ QR  █  Scan to  │
│ ○ [ INPUT 4       ] │ │ ○ [ INPUT 4       ] │ │       ▀▀▀▀▀▀▀  control  │
│ Active:     Input 1 │ │ Active:     Input 3 │ │ [DOWNLOAD ALL SETTINGS] │
└─────────────────────┘ └─────────────────────┘ └─────────────────────────┘
```

**What it does**
- **Source buttons:** 4 for each Aux output, with an LED showing which one is live.
- **Changes made on the switcher's own panel** show up almost at once. The switcher announces them itself, and a poll every 5 s is the safety net.
- **Smart polling** pauses for 2 s after a button press, so polls don't clash with commands.
- **Watchdog:** *Switcher Status* turns OFFLINE if nothing is heard for 5 s.
- **QR code:** visitors scan it to open the dashboard. It uses whatever address you opened it with.
- **DOWNLOAD ALL SETTINGS:** saves every documented switcher setting to JSON with [`tools/mcs8m_dump.py`](../../../tools/mcs8m_dump.py). This is **read-only**.

It has been used on a real MCS-8M driving two projectors: Aux 1 to the audience screen and Aux 2 to a stage display.

## Requirements

- Node-RED 3 or 4. A Raspberry Pi is ideal.
- Install these from the palette (Menu → Manage palette → Install):
  - `node-red-dashboard`. This is the original Dashboard. It's deprecated in favour of Dashboard 2, but still works and is widely installed.
  - `node-red-contrib-ui-led`
  - `node-red-node-serialport`
- For DOWNLOAD ALL SETTINGS: Python 3, and this repo cloned on the same machine, by default at `~/mcs8m-protocol`.
- Serial control enabled on the switcher (Rem 1). See [Getting started](../../../docs/00-getting-started.md).

## Install

1. In Node-RED, open Menu → Import, choose **select a file to import**, pick `mcs8m-dashboard-flow.json`, then click **Import**. It opens on a new tab called **MCS-8M Dashboard**.
2. Double-click any serial node (e.g. **To Switcher**), click the pencil next to **MCS-8M serial port**, and set your port, e.g. `/dev/ttyUSB0`.
3. Double-click **EDIT SETTINGS HERE** (see below).
4. Click **Deploy**, then open `http://<your-node-red>:1880/ui`.

## Settings

Everything is in the **EDIT SETTINGS HERE** node:

| Setting | Default | Meaning |
|---|---|---|
| `source_names` | `{"1":"Input 1", …}` | Names shown on the "Active:" line for switcher inputs 1–14 |
| `buttons` | `[1,2,3,4]` | The source each of the 4 buttons selects, top to bottom. Used to light the right LED. |
| `repo_dir` | `~/mcs8m-protocol` | Where you cloned this repo (for DOWNLOAD ALL SETTINGS) |
| `dump_port` | `/dev/ttyUSB0` | Serial port for DOWNLOAD ALL SETTINGS. Make it the same as the serial config node. |

**To change what a button does**, for example to make button 3 select input 6:
1. Double-click the button and change its payload to `{"aux":1,"source":6}`. Change its label too, e.g. `SOUND DESK`.
2. Do the same for the Aux 2 button, with `"aux":2`.
3. In EDIT SETTINGS HERE, set `buttons` to `[1,2,6,4]`. Optionally set `source_names` to `{"6":"Sound Desk", …}`.
4. Deploy.

You can add more buttons by copying a button-and-LED pair. You'd also need to extend the **LED & Status Controller** node, which handles 4 per Aux.

## How it works

```
buttons ──┬─► Queue (50 ms) ─► Command Builder ─► To Switcher
          └─► Record Activity (pauses polling for 2 s)
Poll Timer (5 s) ─► Smart Poll ─┘

From Switcher ─► Response Parser ─► LED & Status Controller ─► LEDs, "Active:" text
                 (frames by BC, labels each update: button reply / poll reply / panel change)
Check Health (2 s) ─► Watchdog ─► Switcher Status
DOWNLOAD ALL SETTINGS ─► release port ─► python3 tools/mcs8m_dump.py ─► reclaim port ─► toast
```

- The **Response Parser** shows its status under the node in the editor: the latest update and how many *panel changes* it has seen. If you switch an Aux on the switcher's panel and it shows `panel change`, your unit announces Aux changes as well as program changes.
- **Only the Aux A-bus drives the display.** The B-bus (preset) is ignored on purpose; see [video commands](../../../docs/02-video.md).
- **DOWNLOAD ALL SETTINGS** tells the serial node to release the port, runs the dump script (about 31 s), then takes the port back. Buttons and polling are paused meanwhile. Dumps are saved in `<repo_dir>/dumps/`, which git ignores.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Switcher Status stays OFFLINE | Check the port in the serial config node, the null-modem cable, and Serial Cmd Group = Rem 1. See [Getting started](../../../docs/00-getting-started.md#if-nothing-answers). |
| A button works but its LED doesn't light | `buttons` in EDIT SETTINGS HERE doesn't match the button payloads. |
| "Download failed" | Check that `repo_dir` points to the clone and `python3` is installed. The **Dump log** debug node shows the script's error messages. |
| The QR code doesn't appear | It loads a small library from cdnjs. A Node-RED with no internet access shows a message instead. |
