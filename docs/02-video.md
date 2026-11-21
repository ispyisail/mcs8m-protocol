# 2. Video commands

Video frames use PID `01` and TID `20`. The MSG part is:

```
03  count  EFF  CMD  arg1  arg2 …
│   │      │    └─ command (high bit clear = read, set = write/answer)
│   │      └─ section (see below)
│   └─ number of bytes that follow: EFF + CMD + args
└─ PROT, always 03
```

### Sections (EFF)

| EFF | Section | Notes |
|---|---|---|
| `00` | M/E (program/preset, keyer, transitions) | ✅ |
| `01` | not used | 📄 |
| `02` | Aux 1 output | ✅ |
| `03` | Aux 2 output | ✅ |
| `20` | 🔍 undocumented ("system"?) | sends the transition-running messages; see [05](05-undocumented.md) |
| `21` | E-File (snapshots) | 📄 write-only in the manual; 🔍 some reads answer |

Below, **R** is the read request (MSG bytes after `03`), **A** is the answer, which has the same format as a write (**W**), and `ee` is the EFF.

---

## Crosspoint select: `00/80`, `01/81`, `07/87`, `08/88`

Selects the source on a bus.

| | Bytes |
|---|---|
| R | `02 ee 0b` |
| W / A | `04 ee (0b|80) ff nn` |

`0b` is the bus:

| Bus | Code | Sections | Meaning on the MCS-8M |
|---|---|---|---|
| A | `00` | M/E, Aux1, Aux2 | ✅ **M/E: PROGRAM (on air).** Aux: the bus that feeds the output |
| B | `01` | M/E, Aux1, Aux2 | ✅ M/E: PRESET (next) |
| Key fill | `07` | M/E | 📄 |
| Key source | `08` | M/E | 📄 only matters when the key source is set to Split |

`nn` is the crosspoint button, `01`–`0E` (1–14). Buttons 8–14 are SHIFT + 1–7.

`ff` is a flag byte. The manual says it's always `00`, but:

- ✅ the M/E A-bus answers with **`40`**. It's a fixed marker of the program bus and doesn't change when sources are swapped;
- 🔍 some undocumented buses answer with `80`.

When writing, send `00`. When reading, use only `nn` for the source.

**Bus behaviour** ✅: AUTO TRANS swaps the sources between the A and B buses. For example, A = 7 and B = 2 becomes A = 2 and B = 7. So "what's on air" is always the **A-bus**. The dashboard tested here switches the Aux outputs by writing the Aux A-bus (`80`), and that changes the physical output.

📄 For the key, normally write only Key fill (`87`). Write Key source (`88`) as well only when the key source is set to Split.

---

## Next transition: `10/90` (M/E)

| | Bytes |
|---|---|
| R | `02 00 10` |
| W / A | `03 00 90 tt` |

`tt`: `01` BKGD, `02` KEY, `03` BKGD + KEY. ✅ Read verified.

## Transition type: `11/91` (M/E)

| | Bytes |
|---|---|
| R | `04 00 11 fn 00` |
| W / A | `05 00 91 fn 00 ty` |

- `fn`, which transition: `00` effect (background), `01` key.
- `ty`, type: `02` MIX, `04` WIPE, `08` NAM, `14` CUT (key only), `20` DME WIPE.

✅ Read verified. To use a wipe, first choose the pattern (`9B`/`9C`, below), then set the type.

## Transition rate: `18/98`

| | Bytes |
|---|---|
| R | `04 ee 18 fn 00` |
| W / A | `06 ee 98 fn 00 hh tu` |

- `fn`: `00` effect (M/E, Aux1, Aux2), `01` key (M/E), `06` fade to black (M/E).
- The rate is in **frames, as BCD digits**. `hh` holds the hundreds digit in its low nibble; `tu` holds the tens digit (high nibble) and the ones digit (low nibble). So 30 frames is `00 30` and 123 frames is `01 23`.

✅ Read verified (Effect, Key and FTB on M/E; Effect on Aux1 and Aux2).
📄 Sony notes that this rate applies to transitions started from the panel or GPI. A serial AUTO TRANSITION carries its own rate.

## Auto transition start: `96` (write only)

| | Bytes |
|---|---|
| W | `05 ee 96 fn hh tu` |

Runs a transition at the given rate (BCD frames, as above). Rate `00 00` = a cut. `fn` is as for the transition rate. 📄 Not tested over serial.

---

## Wipe patterns and directions

These commands take a **sub-address** byte (`sa`) after CMD, which selects the setting inside the command.

| Setting | CMD (R/W) | `sa` | Value |
|---|---|---|---|
| Wipe pattern | `1B`/`9B` | `00` | 2 bytes, 4 BCD digits |
| Wipe direction | `1B`/`9B` | `04` | 1 byte, bit flags |
| DME wipe pattern | `1C`/`9C` | `00` | 2 bytes, 4 BCD digits |
| DME wipe direction | `1C`/`9C` | `01` | `00` normal, `01` reverse |
| DME wipe normal/reverse | `1C`/`9C` | `02` | `00` off, `01` on |
| Key DME wipe pattern | `1C`/`9C` | `10` | 2 bytes, 4 BCD digits |
| Key DME wipe direction | `1C`/`9C` | `11` | `00` normal, `01` reverse |
| Key DME wipe normal/reverse | `1C`/`9C` | `12` | `00` off, `01` on |
| Key wipe pattern | `33`/`B3` | `00` | 2 bytes, 4 BCD digits |
| Key wipe direction | `33`/`B3` | `04` | 1 byte, bit flags |

All of these are on the M/E (`ee` = `00`). ✅ All reads verified.

| | Bytes |
|---|---|
| R | `03 00 cc sa` |
| W / A (pattern) | `05 00 cc' sa d1 d2`, where pattern = BCD, e.g. 1201 = `12 01` |
| W / A (one-byte value) | `04 00 cc' sa vv` |

(`cc'` is the CMD with its high bit set.)

**Direction bit flags** (wipe and key wipe, `sa` = `04`): bit 2 = REVERSE, bit 1 = NORMAL, bit 0 = NORMAL/REVERSE (alternate each time). ✅ Seen: `02` = normal.

📄 Sony says to send the DME direction pair (`01` and `02`) together.

---

## Snapshots (E-File, EFF `21`)

| Action | W / A |
|---|---|
| Store the current state in register `rr` | `05 21 80 rr 00 00` |
| Recall register `rr` | `05 21 90 rr 00 00` |

`rr` = `01`–`14` (registers 1–20). 📄 Not tested.

> ⚠️ `21 80` **overwrites** a snapshot register. This is the most dangerous mistake if you mix up the read/write bit. Treat the whole `EFF 21` section with care. The probe tool here never sends CMD `00` or `10` to it, because those are the read forms of store and recall.

---

## Undocumented video commands

The MCS-8M also answers many reads the manual doesn't mention: 16 crosspoint bus slots per section, and about 14 parameter groups. See [05-undocumented.md](05-undocumented.md).
