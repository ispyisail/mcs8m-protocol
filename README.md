# Sony MCS-8M RS-232 protocol: an unofficial reference

An independent, tested description of the serial control protocol of the **Sony MCS-8M multi-format compact switcher**.

It fixes the errors in Sony's protocol manual, documents behaviour the manual leaves out, and lists commands the manual doesn't mention that were found by careful read-only probing. It also includes tested tools and examples in Python, Node.js and Node-RED, for Linux, macOS, Raspberry Pi and Windows.

> **Unofficial.** Not affiliated with, endorsed by, or supported by Sony. "Sony" and "MCS-8M" are trademarks of Sony Corporation.
> This document is written from scratch from testing on a real unit. It does not reproduce Sony's manual. For the official command definitions, see Sony's *MCS-8M Protocol Manual* (1st edition, 2011).
> Use at your own risk. Sending write commands changes what's on air.

## Quick reference

| | Sony MCS-8M RS-232C control |
|---|---|
| Port | **REMOTE**, 9-pin D-sub, rear panel. [Pinout](docs/07-hardware.md#the-remote-connector-) |
| Cable | **Null-modem** (crossed). Only TxD, RxD and GND are used |
| Serial settings | **38400 baud, 8N1**, no flow control |
| Switcher setting | **Serial Cmd Group = Rem 1**, saved with Startup Define (off from the factory) |
| Data | **Binary** frames: no ASCII, no line endings |
| Frame | `01 02` · BC (2 bytes, **big-endian**) · PID · `50` · TID · `00 00` · MSG… · CS · `03` |
| Checksum | all bytes from BC through CS add up to `00` (low 8 bits) |
| Video | PID `01`, TID `20`, MSG = `03 count EFF CMD args…`; **read = high bit clear**, write = set |
| Audio | PID `02`, TID `30`, MSG = `CMD data…`; **read = high bit set**, write = clear |
| Timing | video replies after **~1.01 s**, audio after ~11 ms; unknown commands get no reply |
| Parsing | split frames by **BC**, never by `03`, which also appears inside frames |

**Try it.** This asks what's on air (program) and prints the source button number:

```python
import serial                                         # pip install pyserial
s = serial.Serial("/dev/ttyUSB0", 38400, timeout=2)   # "COM3" on Windows; 8N1 is the default
s.write(bytes.fromhex("01 02 00 09 01 50 20 00 00 03 02 00 00 81 03"))   # read program (M/E A-bus)
reply = s.read(17)                                    # video replies take ~1 s
print("on air: button", reply[14])
```

For real use, match replies to requests and frame them by BC. The switcher also sends frames on its own. The [Python library](examples/python/mcs8m.py) handles all of that.

**Machine-readable spec:** [`commands.json`](commands.json) describes the whole protocol in one file: transport, framing, every command with its argument layout and value tables, the messages the switcher sends on its own, the undocumented commands, errata and verified example frames. Each entry carries a confidence label. It's meant for code generators, Companion module authors and AI tools. `tools/check_commands.py` rebuilds every read request from it alone, and checks them against the tested dump tool.

## What you can build

- **Projector or Aux control panels for volunteers.** A [ready-made Node-RED dashboard](examples/node-red/dashboard/) is included.
- **Tally lights**: on-air and preview indicators on ESP32s or phones. The switcher announces program changes and transitions as they happen ([tally example](examples/python/03_tally.py)).
- **Stream Deck / Bitfocus Companion buttons**: cut, auto-transition, mute or route Aux outputs from a button, using Companion's built-in generic modules. There's a [step-by-step guide](docs/09-bitfocus-companion.md) with ready-to-paste commands. (There's no dedicated module with tally feedback yet.)
- **Automation bridges**, from Node-RED, Home Assistant, or show-control software, via a Raspberry Pi or a serial-to-network adapter.
- **Settings backup and audit**: a read-only [dump of every setting](tools/mcs8m_dump.py) to JSON.

## The two things that stop it working

**1. Serial control is off from the factory.** On the switcher, open Setup → System, highlight **Back**, press the **V3** knob, and set **Serial Cmd Group = Rem 1**. Then **save and restart with Startup Define**. Until you do, the switcher silently ignores the serial port: no error, no reply. See [Hardware](docs/07-hardware.md#required-enable-serial-control-on-the-switcher).

**2. The byte count (BC) is big-endian.** Sony's manual says "2 byte Little Endian", and that's wrong. If you follow it, the switcher silently ignores every frame you send. Every example in the manual, and the real unit, use high byte first:

```
01 02 00 0B 01 50 20 00 00 03 04 00 80 00 01 FC 03
      ^^^^^ BC = 0x000B = 11
```

The other corrections are in [docs/04-errata.md](docs/04-errata.md).

## New here?

Start with **[Getting started](docs/00-getting-started.md)**: wiring, enabling serial control, and your first read in about 15 minutes. You can also try everything without a switcher, using the built-in simulator.

```sh
python3 examples/python/01_read_program.py /dev/ttyUSB0
Program (on air): 2
Preset  (next):   7
```

Ready-to-use code in **Python, Node.js and Node-RED** is in [examples/](examples/).

Just want a projector control panel? Import the **[Node-RED dashboard](examples/node-red/dashboard/)**: source buttons for Aux 1 and Aux 2 on any phone or tablet. On a fresh Raspberry Pi, `bash tools/install-pi.sh --dashboard` sets it all up; see [Pi setup](docs/08-pi-setup.md).

## Contents

| Document | What's in it |
|---|---|
| [00 Getting started](docs/00-getting-started.md) | Hardware, enabling Rem 1, first read, troubleshooting, simulator |
| [01 Framing](docs/01-framing.md) | Serial settings, wiring, enabling remote control, frame layout, checksum, timing, how to parse the stream |
| [02 Video commands](docs/02-video.md) | Crosspoints, transitions, wipe patterns and directions, snapshots |
| [03 Audio commands](docs/03-audio.md) | Faders, mute, trim, filters, EQ, pan, monitor |
| [04 Errata](docs/04-errata.md) | Mistakes in the official manual |
| [05 Undocumented](docs/05-undocumented.md) | Commands found by probing, and how to help decode them |
| [06 Examples](docs/06-examples.md) | Real captured frames, annotated byte by byte |
| [07 Hardware](docs/07-hardware.md) | Enabling Rem 1, the REMOTE pinout, null-modem cables, adapters (RS-232 vs TTL), cable length, loopback test |
| [08 Raspberry Pi setup](docs/08-pi-setup.md) | Blank SD card to working controller in ~30 min, securing the editor, backing up your setup, rebuilding after a failure |
| [09 Bitfocus Companion](docs/09-bitfocus-companion.md) | Stream Deck buttons through Companion's Generic TCP Serial and TCP/UDP modules, a ser2net bridge, ready-to-paste hex commands |

## Confidence labels

Everything is tagged with one of these:

| Label | Meaning |
|---|---|
| ✅ **Verified** | Tested on a real MCS-8M |
| 📄 **Manual** | Taken from Sony's manual, but not yet tested here |
| 🔍 **Found** | Undocumented. Seen on the real unit, but what it means isn't known yet |
| ❓ **Guess** | Interpretation, not proven |

Tested on: MCS-8M, main application version 1.01 or later, RS-232 at 38400 8N1 through a USB-serial adapter, November 2026.

## Tools

| Tool | What it does |
|---|---|
| [`tools/mcs8m_dump.py`](tools/mcs8m_dump.py) | Reads every documented setting (118 reads, about 31 s) and writes a JSON file. **Read-only.** |
| [`tools/mcs8m_probe.py`](tools/mcs8m_probe.py) | Searches for undocumented read commands. **Read-only**, can be resumed, and stops if anything unexpected happens. |
| [`tools/values.json`](tools/values.json) | Code-to-value tables (fader dB, EQ frequencies, pan…), made by `tools/gen_values.py` |
| [`tools/install-pi.sh`](tools/install-pi.sh) | One-command Raspberry Pi setup: Node-RED, add-ons, serial permissions, this repo; `--dashboard` or `--restore` |
| [`bridge/`](bridge/) | **Network bridge**: owns the serial port, shares live state and commands over HTTP, video-loop guard, **vMix** connector (lower thirds, follow or control); OBS coming |
| [`tools/check_commands.py`](tools/check_commands.py) | Validates `commands.json` against the tested tools |
| [`tools/companion_commands.py`](tools/companion_commands.py) | Ready-to-paste hex commands for Bitfocus Companion (Aux, program, preset, AUTO TRANS, FTB, mute, snapshot recall) |
| [`tools/loopback_test.py`](tools/loopback_test.py) | Checks your adapter and cable without the switcher (pins 2–3 joined) |
| [`tools/simulator.py`](tools/simulator.py) | A fake MCS-8M on a virtual serial port, for development without hardware |
| [`tools/check_docs.py`](tools/check_docs.py) | Checks the byte count and checksum of every frame quoted in these docs |

The serial tools need Python 3. On Linux, macOS and Raspberry Pi there's nothing else to install. On **Windows**, run `py -m pip install pyserial` first, and use port names like `COM3`. The dump and probe tools refuse to send any command code the protocol treats as a write. (`simulator.py` needs Linux or macOS.)

```sh
python3 tools/mcs8m_dump.py --selftest          # check the frame builder against known frames
python3 tools/mcs8m_dump.py --dry-run           # print what would be sent
python3 tools/mcs8m_dump.py -p /dev/ttyUSB0 -o settings.json
```

## Contributing

Owners of an MCS-8M (or another Sony switcher that uses the same protocol) can help most by **decoding the undocumented commands**. Read everything, change one control on the panel, read again, and report which bytes changed. See [docs/05-undocumented.md](docs/05-undocumented.md#how-to-help).

Please open an issue with:
- your firmware version,
- the exact frames sent and received, as hex,
- what you changed on the panel.

## Licence

- **Code** (`tools/`, `examples/`): MIT. See [LICENSE](LICENSE).
- **Documentation** (`docs/`, the READMEs): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). See [LICENSE-docs](LICENSE-docs).

You can use both freely, including commercially, e.g. in a Bitfocus Companion module. Just credit this repo.
