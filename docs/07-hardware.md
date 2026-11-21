# 7. Hardware: the physical serial connection

Most "it doesn't answer" problems are physical: the wrong cable, the wrong voltage levels, or the wrong port. This page covers what you need and how to test each part.

## Summary

| Item | Requirement |
|---|---|
| Switcher port | **REMOTE**, 9-pin D-sub on the rear panel 📄 |
| Signal standard | **RS-232** (±5 to ±15 V). Not TTL or 3.3 V. |
| Cable | **Null-modem** (crossed): TxD↔RxD swapped, GND to GND |
| Wires actually needed | 3: TxD, RxD, GND 📄 |
| Flow control | **None.** Hardware handshaking isn't supported 📄 |
| Settings | 38400 baud, 8 data bits, no parity, 1 stop bit ✅ |
| Computer side | A USB-to-RS-232 adapter, a built-in COM port, or an RS-232 HAT for a Raspberry Pi |

## Required: enable serial control on the switcher

> 🛑 **If you skip this, nothing works, and there's no error message.** The switcher ignores everything on the REMOTE port. To the controller it looks just like a broken cable. Serial control is **off** from the factory.

The setting is in a hidden service menu on the switcher's own control panel.

1. Open **Setup → System**.
2. Move the cursor down to the **last item, "Back"**.
3. With "Back" selected, **press the V3 knob**. A hidden **For Services** menu opens.
4. Find **Serial Cmd Group** and set it to **Rem 1**.
5. **Save and restart using "Startup Define".** Sony says to do this "without fail". Without it, the change ❓ may not take effect, or may be lost at the next power cycle.
6. Check: after the restart, go back into For Services and confirm Serial Cmd Group still shows **Rem 1**.

✅ Rem 1 is the setting used on the test unit. The flow above is 📄 from Sony's manual. If your menus look different (other firmware versions), please open an issue.

> ⚠️ **Don't change anything else in For Services.** These are service settings; Sony warns that changing them causes unexpected behaviour.
> **Never select FACT** in Serial Cmd Group. It's a factory mode, and Sony says not to use serial control in that state.

### "It worked yesterday, and today it doesn't"

❓ The most likely cause is that the setting was changed but **not saved with Startup Define**, so it went back to the factory value when the switcher was powered off. Do steps 4–6 again.

❓ A factory reset, or loading a different startup setup, would probably put it back too.

### How to tell whether this is the problem

| Test | Result | Meaning |
|---|---|---|
| [Loopback test](#1-the-adapter-loopback-test) | PASS | Your adapter and cable are fine. |
| Then [`01_read_program.py`](../examples/python/01_read_program.py) | no reply | Most likely Rem 1 isn't set or wasn't saved, or the cable is straight rather than null-modem. |
| The switcher's panel | — | Recheck For Services → Serial Cmd Group. |

## The REMOTE connector 📄

Sony's manual lists a standard 9-pin PC-style (DTE) pinout. The switcher **sends on pin 3 and receives on pin 2**, exactly like a PC. That's why a straight cable doesn't work: it would connect the switcher's output to the PC's output.

| Pin | Signal | Direction (from the switcher) | Needed? |
|---|---|---|---|
| 1 | DCD | in | no |
| **2** | **RxD** | **in** (data from the controller) | **yes** |
| **3** | **TxD** | **out** (data to the controller) | **yes** |
| 4 | DTR | out | no |
| **5** | **GND** | signal ground | **yes** |
| 6 | DSR | in | no |
| 7 | RTS | out | no |
| 8 | CTS | in | no |
| 9 | RI | in | no |

❓ **Connector type:** the manual doesn't say whether the REMOTE port has pins (male) or sockets (female). Check yours before buying a cable. A *gender changer* fixes a mismatch.

## The cable

You need a **null-modem** cable. These are sold as "null modem", "crossover" or "RS-232 cross cable". The minimum wiring is:

```
 Switcher (DB-9)              Computer / adapter (DB-9)
   pin 2  RxD  ◄──────────────  pin 3  TxD
   pin 3  TxD  ──────────────►  pin 2  RxD
   pin 5  GND  ───────────────  pin 5  GND
```

- **Full null-modem cables** also cross or loop the handshake lines (DTR/DSR/DCD, RTS/CTS). They work too, because the switcher ignores those lines.
- **Straight-through (modem) cables don't work.** If you only have one, add a *null-modem adapter* (a small in-line DB-9 plug).
- You can make your own with three wires and two DB-9 connectors.

### Cable length

RS-232 is meant for short runs. At 38400 baud, **keep the cable under about 10–15 m** using ordinary cable. For longer distances:

- **Recommended:** put the computer (e.g. a Raspberry Pi) next to the switcher with a short cable, and reach it over the network. Phones and tablets connect to the dashboard over Wi-Fi or Ethernet anyway.
- Or use an RS-232 extender, such as an over-Cat5 pair or RS-232-to-Ethernet (serial device server). These are untested here, but the protocol doesn't depend on tight timing, so they should be fine.

## The computer side

### USB-to-RS-232 adapters

Any adapter that outputs **real RS-232 levels** with a DB-9 plug will work. Common chipsets:

| Chipset | Notes |
|---|---|
| **FTDI** (FT232R, FT231X…) | The most reliable choice. Drivers are built into Linux, macOS and Windows. |
| **Prolific** PL2303 | Works on Linux and macOS. Counterfeit chips are common and newer Windows drivers reject them. |
| **WCH** CH340 / CH341 | Cheap and usually fine. Built into Linux; older macOS versions need a driver. |

Tested with: ❓ *to be filled in: the adapter and cable used on the test unit.*

> ⚠️ **"TTL", "3.3 V" or "5 V" USB-serial cables** (bare wires, or a 6-pin header, often sold for Arduino or Raspberry Pi consoles) **are not RS-232.** They won't talk to the switcher, and RS-232 voltages can damage them.

### Raspberry Pi

- **The simplest option** is a USB-to-RS-232 adapter, which shows up as `/dev/ttyUSB0`.
- **The Pi's GPIO serial pins are 3.3 V TTL.** Never connect them straight to the switcher. If you want to use them, fit an **RS-232 HAT** or a **MAX3232 module**, which converts the levels. You also need to free the port: `sudo raspi-config` → Interface → Serial, then disable the login shell and enable the hardware port. The port is then `/dev/serial0`. This setup hasn't been tested here.

### Finding the port, and keeping its name stable

```sh
ls -l /dev/serial/by-id/           # Linux: one stable name per USB adapter
dmesg | grep -i tty | tail         # shows what was detected when you plugged it in
ls /dev/cu.*                       # macOS
```

`/dev/ttyUSB0` can turn into `/dev/ttyUSB1` after a reboot, or when another USB serial device is plugged in. For something that runs unattended, such as Node-RED, use the **`/dev/serial/by-id/…`** path instead. It always refers to the same adapter.

On Linux, your user must be in the `dialout` group: `sudo usermod -aG dialout $USER`, then log out and back in.

## Testing step by step

Work outwards from the computer, so you know which part is at fault.

### 1. The adapter: loopback test

Disconnect the switcher. At the end of your cable, connect **pin 2 to pin 3**, using a jumper wire, a paper clip, or a DB-9 loopback plug. Then run:

```sh
python3 tools/loopback_test.py /dev/ttyUSB0
```

```
PASS: 256/256 bytes came back unchanged at 38400 8N1.
```

**PASS** means the adapter, the driver, the port name, your permissions and that cable are all working. **FAIL with nothing received** points to a wrong port, a broken cable, or a TTL adapter.

Run the test at the far end of the cable, at the plug that would go into the switcher. With a null-modem cable, pins 2 and 3 there are still the two data lines, so the test checks the whole cable.

### 2. The switcher

Reconnect the switcher, make sure **Serial Cmd Group = Rem 1** (see [Getting started](00-getting-started.md#step-1-enable-serial-control-on-the-switcher)), and run:

```sh
python3 examples/python/01_read_program.py /dev/ttyUSB0
```

| Result | Meaning |
|---|---|
| Sources are printed | Everything works. |
| No reply, but the loopback passed | Check Rem 1, then **null-modem vs straight cable**: try adding a null-modem adapter. |
| Garbage, or checksum errors | Wrong baud rate or format, or a TTL adapter. It must be 38400 8N1. |

## Good practice

- **Only one program at a time** can use the port. Stop Node-RED before running the command-line tools. The dashboard's DOWNLOAD ALL SETTINGS button does this for you.
- **Don't hot-plug while live.** Connecting or disconnecting the cable mid-show is unlikely to cause trouble, but avoid it. A noise burst on the line is just a frame with a bad checksum, and the switcher discards it.
- **Grounding:** ordinary RS-232 links the two devices' grounds. If you hear hum or see interference after connecting, power both devices from the same mains circuit, or use an **isolated** USB-RS-232 adapter.
