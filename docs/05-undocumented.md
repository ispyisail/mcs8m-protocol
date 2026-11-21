# 5. Undocumented commands

These were found with [`tools/mcs8m_probe.py`](../tools/mcs8m_probe.py), which sends only **read** codes (the high bit clear on video, set on audio), never write codes, and stops if the switcher reports any change it can't explain.

- Scan: November 2026, quick mode. 633 video reads and 40 audio reads, 127 replies.
- Every value below is **what the unit returned at that moment**. Without a before-and-after comparison, the meanings are unknown (🔍), unless marked otherwise.
- Unknown commands get **no reply**, so a reply means the firmware recognises the command.

Notation: the request is the MSG bytes after `03` (count, EFF, CMD, …). The reply data is the bytes after the answer's CMD.

---

## Video: 16 crosspoint bus slots per section 🔍

Reads of CMD `00`–`0F` are answered on **every section** (M/E, Aux1, Aux2), always in the crosspoint answer format `04 ee 8b ff nn`. The manual documents only buses `00`, `01`, `07` and `08`.

| Bus (CMD) | M/E | Aux1 | Aux2 | Notes |
|---|---|---|---|---|
| `00` | `40 04` | `00 06` | `00 06` | A-bus ✅ (current sources at scan time) |
| `01` | `00 06` | `00 06` | `00 07` | B-bus ✅ |
| `02`–`06` | `00 01` | `00 01` | `00 01` | 🔍 |
| `07` | `00 07` | `00 01` | `00 01` | key fill (M/E) ✅ |
| `08` | `00 07` | `80 01` | `80 01` | key source (M/E) ✅; flag `80` on the Aux sections |
| `09`, `0B`, `0D`, `0F` | `00 01` | `00 01` | `00 01` | 🔍 |
| `0A`, `0C`, `0E` | `80 01` | `80 01` | `80 01` | 🔍 flag `80` |

❓ **Interpretation:** it looks like a generic 16-bus crosspoint map shared by every section, probably from a larger Sony switcher design that the MCS-8M firmware is based on. Unused buses sit at button 1. The `80` flag may mean "bus not assigned". This flag appears on Aux key source, where the Aux sections have no keyer, which supports that reading.

## Video: parameter groups `[sub-address, value-hi, value-lo]` 🔍

When these CMDs are read **with no sub-address**, the answer is `05 ee cc' 00 hh ll`: sub-address `00` followed by a 16-bit value. That's the same shape as the documented WIPE PATTERN (`1B`) and KEY WIPE PATTERN (`33`).

| CMD | Answer data (M/E, Aux1, Aux2 all the same) | Notes |
|---|---|---|
| `1B`, `33` | `00 00 01` | documented: pattern = 1 ✅ |
| **`2B`** | `00 80 00` | value `0x8000`, a midpoint. ❓ a position or centre parameter |
| `30`, `32`, `37`, `38`, `3A`, `3B`, `40`, `42`, `43`, `48`, `4A`, `4B` | `00 00 01` | 🔍 |
| `3F` | `00 00 01` | 🔍 answered on M/E only |

These groups probably hold several settings each, at different sub-addresses (`01`, `02` … as `1C` does). The quick scan didn't try sub-addresses, so **the next step is a sub-address scan** (probe full mode, phase 4).

❓ The identical `00 00 01` everywhere, including on the Aux sections, which have no keyer or wipes, suggests many of these are unused placeholders that answer with a default value. `2B` is the one with a distinctive value.

## Video: single-byte status 🔍

| Read | Answer data | Notes |
|---|---|---|
| `02 ee 10` | `01` | NEXT TRANSITION (documented) ✅ |
| `02 ee 13` | `00` | 🔍 |
| `02 ee 15` | `00` | 🔍 |

## Section EFF `20` 🔍

Not mentioned in the manual. The unit uses it for the **transition-running** messages it sends on its own (see [01-framing.md](01-framing.md#messages-the-switcher-sends-on-its-own)):

```
03 05 20 B7 00 00 01     transition started
03 05 20 B7 00 00 00     transition finished
```

That's the `[sub, hi, lo]` parameter-group shape, at CMD `37` (written form `B7`), sub-address `00`. ❓ `EFF 20 CMD 37` looks like a "transition in progress" status, though a direct read of it didn't answer in the scan.

Reads that did answer:

| Request | Answer data |
|---|---|
| `02 20 00` | `FF` |
| `02 20 09` | `FF` |
| `02 20 3A` | `00 00` |
| `02 20 54` | `00` |

## Section EFF `21` (E-File / snapshots) 🔍

The manual lists only writes for this section (store `80`, recall `90`). Some reads answer:

| Request | Answer data | Notes |
|---|---|---|
| `02 21 08` | `00 00 00 00` | 🔍 |
| `02 21 09` | `00 01` | ❓ last recalled or stored register = 1? |
| `02 21 12` | `00 00 00` | 🔍 |

> ⚠️ Never send CMD `80`–`FF` to EFF `21`: `80` is SNAPSHOT STORE and overwrites a register. The probe also avoids reading `00` and `10` here, because they're the read forms of store and recall, and nobody knows how the firmware handles them.

## Audio 🔍

All documented commands answer. Two more answer as well:

| CMD (read as) | Answer for CH1 | Position | Ideas |
|---|---|---|---|
| `15` (`95`) | `00` | between TRIM (`14`) and LOW CUT (`17`) | ❓ input select, phase invert, phantom power? |
| `16` (`96`) | `00` | | ❓ |

✅ The *reserved* byte in the channel-fader reply (data byte 6) reads `EE` (238, which would be +7.1 dB on the fader scale).

Audio commands `28`–`7F` haven't been scanned yet.

## Not found

No reads were found that return **setup-menu configuration**: input names, video formats, serial settings, or which input feeds which audio channel. ❓ The serial protocol seems to expose only live operating state.

---

## How to help

The most useful contribution is a **before-and-after comparison**:

1. Read the undocumented values (for example `python3 tools/mcs8m_probe.py --quick --fresh`, or a targeted read).
2. Change **one** control on the panel: a wipe position, key clip or gain, snapshot recall, audio phase, and so on.
3. Read again and note which values changed.

Report the firmware version, the control you changed, and the exact hex before and after.

Ideas worth testing:

| Unknown | Try changing |
|---|---|
| `2B` (`0x8000`) | wipe position, key position, DME position |
| groups `30`–`4B` | key clip, gain, density, border width or colour, matte colours |
| `13`, `15` | key on/off, transition in progress, FTB active |
| `21 09` | recall or store a snapshot |
| audio `15`, `16` | per-channel input settings in the ACCESS menu |
| fader reserved byte | any audio input or channel setting |
