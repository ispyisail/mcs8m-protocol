# 6. Annotated examples

All frames were captured from a real MCS-8M ✅. The checksums are checked automatically (see `tools/check_docs.py`).

## Video: read the program source (M/E A-bus)

```
TX  01 02 | 00 09 | 01 50 20 00 00 | 03 02 00 00         | 81 | 03
    DEL   | BC=9  | PID FID TID    | PROT n=2 EFF=M/E    | CS | ETX
          |       |   ARQ ART      |   CMD=00 (A-bus read)

RX  01 02 | 00 0B | 01 50 20 00 00 | 03 04 00 80 40 02   | BB | 03
                                     │  │  │  │  │  └ button 2
                                     │  │  │  │  └ flag 40 = program bus marker
                                     │  │  │  └ 80 = A-bus (answer form)
                                     │  │  └ EFF 00 = M/E
                                     │  └ 4 bytes follow
                                     └ PROT
```

The reply took 1012 ms.

**Checksum check:** add the bytes from BC through the MSG: `00+0B+01+50+20+00+00+03+04+00+80+40+02` = `0x145`. The low byte is `45`. `45 + BB = 100`, so the low byte is `00` ✓.

## Video: read the Aux2 output source

```
TX  01 02 00 09 01 50 20 00 00 03 02 03 00 7E 03
RX  01 02 00 0B 01 50 20 00 00 03 04 03 80 00 07 F3 03     → Aux2 = button 7
```

The EFF here is `03`, the same as PROT and ETX. This is why you can't split frames on `03`.

## Video: read the effect transition rate

```
TX  01 02 00 0B 01 50 20 00 00 03 04 00 18 00 00 65 03
RX  01 02 00 0D 01 50 20 00 00 03 06 00 98 00 00 00 30 B1 03
                                          │  │  └──┴ BCD 0030 = 30 frames
                                          │  └ fixed 00
                                          └ 00 = effect transition
```

## Video: read the transition type

```
TX  01 02 00 0B 01 50 20 00 00 03 04 00 11 00 00 6C 03
RX  01 02 00 0C 01 50 20 00 00 03 05 00 91 00 00 02 E8 03     → 02 = MIX
```

## Video: read the DME wipe pattern

```
TX  01 02 00 0A 01 50 20 00 00 03 03 00 1C 00 63 03
RX  01 02 00 0C 01 50 20 00 00 03 05 00 9C 00 12 01 CC 03     → BCD 1201 = pattern 1201
```

## Video: transition running (sent by the switcher on its own)

```
RX  01 02 00 0C 01 50 20 00 00 03 05 20 B7 00 00 01 A3 03     → transition started
RX  01 02 00 0C 01 50 20 00 00 03 05 20 B7 00 00 00 A4 03     → transition finished
```

## Audio: read all channel faders

```
TX  01 02 00 0E 02 50 30 00 00 90 00 00 00 00 00 00 00 00 E0 03
RX  01 02 00 0E 02 50 30 00 00 10 00 00 00 00 00 BE EE BE F6 03
                                  CH1 CH2 CH3 CH4 CH5 CH6 rsv PGM
                                  −∞  −∞  −∞  −∞  −∞ −0.6  ?  −0.6 dB
```

`BE` = 190 = −0.6 dB. `EE` is the undocumented reserved byte.

## Audio: read a master fader

```
TX  01 02 00 08 02 50 30 00 00 92 00 00 E4 03
RX  01 02 00 08 02 50 30 00 00 12 00 14 50 03     → MIX-1/2, code 20 = 0 dB
```

## Audio: read mute

```
TX  01 02 00 08 02 50 30 00 00 93 05 00 DE 03     (status bit set, CH6)
RX  01 02 00 08 02 50 30 00 00 13 05 00 5E 03     → 0 = live (CH ON lit)
```

## Audio: read monitor select

```
TX  01 02 00 08 02 50 30 00 00 C3 00 00 B3 03
RX  01 02 00 08 02 50 30 00 00 43 00 01 32 03     → 1 = PGM-L/R
```

## Write examples (📄 from the manual's formats; frames built and checksummed here)

> ⚠️ These **change the switcher**. Don't send them during a live show unless you mean to.

```
Aux1 A-bus → button 1      01 02 00 0B 01 50 20 00 00 03 04 02 80 00 01 FA 03   ✅ tested (dashboard)
M/E next transition BKGD   01 02 00 0A 01 50 20 00 00 03 03 00 90 01 EE 03
Mute CH1 (1 = muted)       01 02 00 08 02 50 30 00 00 13 00 01 62 03
```
