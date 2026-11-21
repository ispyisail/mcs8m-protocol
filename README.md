# Sony MCS-8M RS-232 protocol: an unofficial reference

An independent, tested description of the serial control protocol of the **Sony MCS-8M multi-format compact switcher**.

It fixes the errors in Sony's protocol manual, documents behaviour the manual leaves out, and lists commands the manual doesn't mention that were found by careful read-only probing. It also includes small read-only tools in plain Python with no extra libraries.

> **Unofficial.** Not affiliated with, endorsed by, or supported by Sony. "Sony" and "MCS-8M" are trademarks of Sony Corporation.
> This document is written from scratch from testing on a real unit. It does not reproduce Sony's manual. For the official command definitions, see Sony's *MCS-8M Protocol Manual* (1st edition, 2011).
> Use at your own risk. Sending write commands changes what's on air.

## The one thing you must know

**The byte count (BC) is big-endian.** Sony's manual says "2 byte Little Endian", and that's wrong. If you follow it, the switcher silently ignores every frame you send. Every example in the manual, and the real unit, use high byte first:

```
01 02 00 0B 01 50 20 00 00 03 04 00 80 00 01 FC 03
      ^^^^^ BC = 0x000B = 11
```

The other corrections are in [docs/04-errata.md](docs/04-errata.md).

## Contents

| Document | What's in it |
|---|---|
| [01 Framing](docs/01-framing.md) | Serial settings, wiring, enabling remote control, frame layout, checksum, timing, how to parse the stream |
| [02 Video commands](docs/02-video.md) | Crosspoints, transitions, wipe patterns and directions, snapshots |
| [03 Audio commands](docs/03-audio.md) | Faders, mute, trim, filters, EQ, pan, monitor |
| [04 Errata](docs/04-errata.md) | Mistakes in the official manual |
| [05 Undocumented](docs/05-undocumented.md) | Commands found by probing, and how to help decode them |
| [06 Examples](docs/06-examples.md) | Real captured frames, annotated byte by byte |

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
| [`tools/check_docs.py`](tools/check_docs.py) | Checks the byte count and checksum of every frame quoted in these docs |

The two serial tools need Python 3 on Linux or macOS and nothing else (they use `termios`, not pyserial). Both refuse to send any command code the protocol treats as a write.

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

- Documentation (`docs/`, this README): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- Code (`tools/`): MIT. See [LICENSE](LICENSE).
