# 4. Errata: mistakes in Sony's MCS-8M Protocol Manual (1st edition, 2011)

References are to sections of the official manual.

| # | Where | The manual says | Correct | Impact |
|---|---|---|---|---|
| 1 | §2-3 Data Format, BC row | BC is "2 byte **Little** Endian" | ✅ **Big-endian** (high byte first). Every example frame in the manual is already big-endian (`01 02 00 0B …`). | **Critical.** A little-endian BC makes the switcher silently discard every frame. |
| 2 | §4-3, status-request example | Reply `… 17 00 01 5F 03`, described as "LOW CUT FILTER is OFF" | The checksum should be `5E`, and data `01` would mean ON. The example contradicts itself. | Copying it gives a frame with a bad checksum. |
| 3 | EQ LOW IN example | Uses CMD `17` | Should be `19` (`17` is LOW CUT FILTER). | The example sets the wrong control. |
| 4 | SNAPSHOT STORE/RECALL register table | "0x01: 2" | `0x02: 2` | Typo |
| 5 | AUTO TRANSITION START note | "specify byte5 and byte6" | It means byte4 and byte5; that command has no byte6. | Confusing |
| 6 | CHANNEL FADER values | Codes 60 and 61 are both −34.2 dB | One of them is probably a typo (the scale suggests about −34.6 dB for code 60). ❓ | Minor |
| 7 | MUTE | Data "OFF 0 / ON 1" | ✅ **`1` = muted** (CH ON dark), `0` = live (CH ON lit) | Easy to wire up the wrong way round |
| 8 | XPT SELECT | Implies byte3 is always `00` | ✅ The M/E A-bus answers `40`, and some undocumented buses answer `80`. | Parsers that check for `00` reject valid replies. |

## Missing from the manual

These are behaviours the manual doesn't mention but any controller needs to handle. Details are in [01-framing.md](01-framing.md).

- ✅ `03` (ETX) also appears inside frames, so you must frame by BC.
- ✅ Video reads take about 1 s to answer. Audio reads take about 11 ms.
- ✅ Unknown commands get no reply at all.
- ✅ Replies are sometimes sent twice, about 1.5 s apart.
- ✅ The switcher sends status frames on its own when the panel changes, including an undocumented "transition running" message.
- ✅ The read/write bit is reversed between video and audio. The manual says this, but in two separate places, and it's easy to miss.
- ✅ On the M/E, the A-bus is program (on air) and the B-bus is preset.

Found another error? Please open an issue with the page or section, and the evidence.
