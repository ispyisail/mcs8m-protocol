# 3. Audio commands

Audio frames use PID `02` and TID `30`. The MSG part is just `CMD, data…`. Unlike video, there's no PROT or count byte.

| Request | CMD byte | Switcher's answer |
|---|---|---|
| **Write** (execute) | command code, high bit **clear** | 📄 a single `04` byte (ACK), not a frame |
| **Read** (status) | command code **+ `0x80`** | ✅ a full frame in the write format, carrying the current value |

For a read, send the same data bytes you'd send for a write. The channel byte selects the channel, and the value byte is ignored (✅ send `00`). Replies arrive in about 11 ms. ✅

Channels are numbered `00`–`05` for CH1–CH6.

> ✅ All reads below were tested on a real unit. Writes are 📄 from the manual and haven't been tested here.

## Command summary

| CMD | Read as | Setting | Data | Values |
|---|---|---|---|---|
| `10` | `90` | Channel faders, all at once | 8 bytes | fader codes (see below) |
| `12` | `92` | Master fader | `ch`, `lv` | `ch`: 0 MIX-1/2, 1 MIX-3/4, 2 AUX-1, 3 AUX-2; `lv`: 0–30 |
| `13` | `93` | Mute | `ch`, `v` | ✅ **`1` = muted**, `0` = live (see note) |
| `14` | `94` | Trim | `ch`, `v` | 0–30 → −15 … +15 dB (1 dB steps, 15 = 0 dB) |
| `15` | `95` | 🔍 undocumented | `ch`, `v` | reads `00`; see [05](05-undocumented.md) |
| `16` | `96` | 🔍 undocumented | `ch`, `v` | reads `00` |
| `17` | `97` | Low-cut filter | `ch`, `v` | 0 off, 1 on |
| `18` | `98` | High-cut filter | `ch`, `v` | 0 off, 1 on |
| `19` | `99` | EQ low on/off | `ch`, `v` | 0 off, 1 on |
| `1B` | `9B` | EQ low frequency | `ch`, `v` | 0–30 → 31 Hz … 420 Hz (default 15 = 114.1 Hz) |
| `1C` | `9C` | EQ low gain | `ch`, `v` | 0–30 → −15 … +15 dB |
| `1D` | `9D` | EQ mid on/off | `ch`, `v` | 0 off, 1 on |
| `1E` | `9E` | EQ mid frequency | `ch`, `v` | 0–30 → 260 Hz … 6.4 kHz (default 15 = 1.28 kHz) |
| `1F` | `9F` | EQ mid gain | `ch`, `v` | 0–30 → −15 … +15 dB |
| `20` | `A0` | EQ high on/off | `ch`, `v` | 0 off, 1 on |
| `22` | `A2` | EQ high frequency | `ch`, `v` | 0–30 → 1.3 kHz … 17.4 kHz (default 15 = 4.75 kHz) |
| `23` | `A3` | EQ high gain | `ch`, `v` | 0–30 → −15 … +15 dB |
| `33` | `B3` | Pan | `ch`, `v` | 0 = 15L … 15 = centre … 30 = 15R |
| `40` | `C0` | Monitor level | `00`, `v` | 0–255, same scale as the channel faders |
| `41` | `C1` | Monitor dim | `00`, `v` | 0 off, 1 on |
| `43` | `C3` | Monitor select | `00`, `v` | 1 PGM-L/R, 2 MIX-L/R, 4 AUX-1, 5 AUX-2 |

The full code-to-value tables (fader dB, EQ frequencies and so on) are in [`tools/values.json`](../tools/values.json).

## Channel faders (`10`)

A single command carries all the faders:

| Data byte | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| Fader | CH1 | CH2 | CH3 | CH4 | CH5 | CH6 | *reserved* | PGM |

- Each byte is a code from 0 to 255: **0 = −∞, 194 = 0 dB, 255 = +10 dB**. The scale isn't linear (roughly 0.8 dB per step at the bottom and 0.15 dB per step near 0 dB); use the table in `values.json`.
- ✅ The *reserved* byte reads **`EE`** (238) on the tested unit. Its meaning is unknown. When writing, send back the value you read.
- 📄 The MCS-8M doesn't have motorised faders. A serial write changes the level, but the physical fader stays where it is, so moving it on the panel jumps the level back.

## Master fader (`12`)

Level code 0–30: 0 = −∞, then −60, −52, −48 dB, then 4 dB steps up to −20 dB, then 2 dB steps to 0 dB (code 20, the default), then 1 dB steps to +10 dB.

📄 This is the output level setting in the SETUP/AUDIO menu, not a physical fader.

## Mute (`13`): reversed meaning ✅

Sony's manual describes the data byte as "OFF 0 / ON 1", which reads as "channel on". In fact, **`1` means the channel is muted** (the CH ON button is dark), and **`0` means it's live** (CH ON lit). This was confirmed by comparing the readings with the panel.

## Example

Read CH6 mute:

```
TX  01 02 00 08 02 50 30 00 00 93 05 00 DE 03
RX  01 02 00 08 02 50 30 00 00 13 05 00 5E 03     → CH6 = 0 = live
```

More examples are in [06-examples.md](06-examples.md).
