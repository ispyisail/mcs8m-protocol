# 8. Raspberry Pi setup (and rebuilding after a failure)

How to go from a blank SD card to a working MCS-8M controller in about 30 minutes, and how to get **your own** setup back if the Pi or its SD card dies.

## What you need

- A Raspberry Pi 3, 4 or 5 (a Pi Zero 2 W works, but slowly), with its power supply
- A microSD card (16 GB or more; a good brand such as SanDisk or Samsung)
- A USB-to-RS-232 adapter and a null-modem cable (see [Hardware](07-hardware.md))
- A network connection: Ethernet is the most reliable

## 1. Prepare the SD card

On any computer, install **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)**, then:

1. **Device:** your Pi model. **OS:** *Raspberry Pi OS Lite (64-bit)*. Lite is enough, because Node-RED needs no desktop. Use the full OS if you also want a screen on the Pi.
2. Click **Edit settings** (the ⚙️ button). This saves you needing a keyboard and screen:
   - **Hostname:** e.g. `nodered`. You'll then reach it as `nodered.local`.
   - **Username and password:** choose your own and write them down.
   - **Wi-Fi:** only if you're not using Ethernet.
   - **Services:** turn on **SSH** with password authentication.
3. Write the card, put it in the Pi, and power it up. The first boot takes a couple of minutes.

## 2. Log in

From a computer on the same network:

```sh
ssh <username>@nodered.local          # or ssh <username>@<the Pi's IP address>
```

> **Tip:** give the Pi a fixed IP address, a *DHCP reservation* in your router. Then the dashboard address and its QR code never change.

## 3. Run the install script

```sh
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/ispyisail/mcs8m-protocol.git
bash mcs8m-protocol/tools/install-pi.sh --dashboard
sudo reboot
```

[`tools/install-pi.sh`](../tools/install-pi.sh) does the following. Each step is skipped if it's already done, so it's safe to run again.

| Step | What it does |
|---|---|
| 1 | installs `git`, `python3` and `curl` |
| 2 | installs Node.js and Node-RED with the **official Node-RED installer**. That also gives you `node-red-start`, `node-red-stop`, `node-red-restart` and `node-red-log` |
| 3 | adds you to the `dialout` group, so you can use the serial port (takes effect after the reboot) |
| 4 | clones or updates this repo in `~/mcs8m-protocol` |
| 5 | installs the add-ons: `node-red-dashboard`, `node-red-contrib-ui-led`, `node-red-node-serialport` |
| 6 | `--dashboard`: loads the [ready-made dashboard](../examples/node-red/dashboard/), but only if there's no flow yet |
| 7 | makes Node-RED start on boot, and starts it |

Use `--dry-run` to see what it would do without changing anything.

## 4. Finish off

1. Plug in the USB serial adapter, then check its name: `ls -l /dev/serial/by-id/`
2. Open the editor at `http://nodered.local:1880`:
   - set the serial port in the **MCS-8M serial port** config node. The `/dev/serial/by-id/…` name is best, because it never changes;
   - double-click **EDIT SETTINGS HERE** to set your input names and buttons;
   - click **Deploy**.
3. Make sure the switcher has **Serial Cmd Group = Rem 1** ([Hardware](07-hardware.md#required-enable-serial-control-on-the-switcher)).
4. Open `http://nodered.local:1880/ui`. *Switcher Status* should say **ONLINE**.

## 5. Recommended: secure the editor

By default, anyone on your network can open the Node-RED editor and change the flows. Set an admin password:

```sh
node-red admin hash-pw        # type a password; copy the hash it prints
nano ~/.node-red/settings.js  # find "adminAuth", uncomment it, and paste the hash in
node-red-restart
```

The dashboard (`/ui`) stays open for your volunteers. Only the editor asks for the password.

---

## Back up your own setup

The steps above give you the **generic** dashboard. Once you've customised it (input names, button labels, layout), that work lives only on the Pi's SD card. SD cards do fail, especially after power cuts, so back up three things:

| What | Where | Why |
|---|---|---|
| `flows.json` | `~/.node-red/` | your flow: everything you built in the editor |
| `package.json` | `~/.node-red/` | the list of add-ons you installed |
| `settings.js` (and `flows_cred.json`, if you have it) | `~/.node-red/` | your settings, and the key that decrypts saved passwords |

> ⚠️ `settings.js` contains your `credentialSecret`. Back it up somewhere **private**, never in a public repo.

The recommended approach is a **private GitHub repo** that your Pi updates automatically every night. Lay it out like this, so that `install-pi.sh --restore` can read it:

```
my-backup/
├── node-red/
│   ├── flows.json
│   ├── package.json
│   └── settings.js
└── mcs8m-tools/        (optional: any scripts, dumps or notes of your own)
```

A Node-RED editor export (Menu → Export → all flows → Download) also works for `flows.json`, but you have to remember to do it.

## Rebuild after a failure

With a backup in that layout:

1. Do steps **1–2** above on a new SD card.
2. Get your backup onto the new Pi. For a private GitHub repo:
   ```sh
   sudo apt-get update && sudo apt-get install -y git gh
   gh auth login                              # approve with a code, in your browser
   gh repo clone <you>/<your-backup-repo> ~/my-backup
   ```
   You can also download it as a ZIP on another computer and copy it over with `scp`.
3. Install everything and restore:
   ```sh
   git clone https://github.com/ispyisail/mcs8m-protocol.git
   bash mcs8m-protocol/tools/install-pi.sh --restore ~/my-backup
   sudo reboot
   ```
   `--restore` reinstalls **every add-on you had** (from your `package.json`), then puts back your flow, settings and tools. Any existing flow is kept as `flows.json.before-restore-<date>`.
4. Check the serial port name (`ls -l /dev/serial/by-id/`). A replacement adapter has a different name, so update the serial config node if needed.

## Even faster: a spare SD card

Once everything works, make a full copy of the SD card. Then recovery is just swapping cards: about 5 minutes.

- **Easiest:** shut the Pi down and put its card in a computer. Save an image with *Win32 Disk Imager* (Windows, "Read"), or with `dd` (Linux or macOS). Write that image to a second card with Raspberry Pi Imager ("Use custom"), and label it.
- **Without removing the card:** `rpi-clone` copies the running system to a second card in a USB reader.

Remake the spare after big changes. The nightly backup covers the small ones.
