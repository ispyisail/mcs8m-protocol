# 1. Connection and framing

## Serial settings ✅

| Setting | Value |
|---|---|
| Speed | 38400 baud |
| Format | 8 data bits, no parity, 1 stop bit |
| Flow control | None. Hardware handshaking isn't supported. |
| Connector | REMOTE (9-pin D-sub) on the rear panel |
| Cable | Null-modem (crossed) cable to a PC. Only TxD, RxD and GND are needed. |

## Enabling remote control 📄

Serial control is **off** from the factory and must be enabled in a service menu.

1. Go to **Setup → System**, move the cursor to the bottom item **Back**, and press the **V3** knob. The **For Services** menu opens.
2. Set **Serial Cmd Group** to **Rem 1**.
3. Save with **Startup Define**, then restart the switcher.

> ⚠️ Don't change anything else in For Services. In particular, never select **FACT** in Serial Cmd Group: it's a factory mode, and Sony warns that it causes unexpected behaviour.

## Frame layout ✅

Every message, in both directions, has the same envelope:

| Offset | Field | Value | Notes |
|---|---|---|---|
| 0 | DEL | `01` | start delimiter |
| 1 | STX | `02` | |
| 2–3 | **BC** | 2 bytes, **big-endian** | number of bytes from PID through the last MSG byte |
| 4 | PID | `01` video, `02` audio | protocol ID |
| 5 | FID | `50` | "from" ID. The switcher's replies also carry `50`. |
| 6 | TID | `20` video, `30` audio | "to" ID |
| 7 | ARQ | `00` | reserved |
| 8 | ART | `00` | reserved |
| 9 … | MSG | command bytes | see [video](02-video.md) and [audio](03-audio.md) |
| n−2 | CS | checksum | |
| n−1 | ETX | `03` | end marker |

Total frame length = BC + 6.

### Byte count ✅

BC counts PID, FID, TID, ARQ, ART and the MSG bytes. That's 5 + the MSG length. It doesn't count DEL, STX, BC itself, CS or ETX.

**BC is big-endian** (high byte first). Sony's manual says little-endian, and that's wrong; see [errata #1](04-errata.md). The high byte is `00` in practice, because no frame is longer than 255 bytes.

### Checksum ✅

CS is chosen so that **every byte from BC (both bytes) through CS adds up to `00`**, keeping only the low 8 bits.

```python
def checksum(body):            # body = BC hi, BC lo, PID … last MSG byte
    return (-sum(body)) & 0xFF
```

To check a received frame: `sum(frame[2:-1]) & 0xFF == 0`.

## Two message families

| | Video | Audio |
|---|---|---|
| PID / TID | `01` / `20` | `02` / `30` |
| MSG shape | `03, count, EFF, CMD, args…` | `CMD, data…` |
| Read | CMD with high bit **clear** (`0x0n`, `0x1n` …) | CMD with high bit **set** (`0x8n` …) |
| Write | CMD with high bit **set** | CMD with high bit **clear** |
| Reply to a read | full frame, CMD shown in **write** form | full frame, CMD shown in **write** form |
| Typical reply time | **~1.01 s** | **~11 ms** |

> ⚠️ **The read/write bit is reversed between video and audio.** ✅ On video, setting the high bit makes a command a *write*. For example, video EFF `21` CMD `80` is SNAPSHOT STORE, and it overwrites a memory register. Never "set the high bit to read" on a video command.

In both families, the switcher answers a read with a frame in the same format a write would use, as if it were reporting "the value is now …".

## Reading the incoming data ✅

**Find frame boundaries with BC, never with `03`.** The byte `03` appears all the time inside frames: the video PROT byte is always `03`, Aux2's EFF is `03`, and values and checksums can be `03`. Splitting the stream on `03` breaks frames apart.

A robust receive loop:

1. Throw bytes away until you see `01 02`.
2. Read BC (big-endian). If it's implausible (below 5 or very large), drop one byte and search again.
3. Wait for BC + 6 bytes in total.
4. Accept the frame only if the last byte is `03` **and** the checksum adds up to zero. If not, drop one byte and search again.
5. A lone `04` byte outside a frame is an **ACK** (📄 the switcher sends it after an audio write).

> Node-RED users: the serial-in node's "split on character" option can't do this safely. Use a function node that buffers the incoming data and frames by BC.

## Timing ✅

| Observation | Detail |
|---|---|
| Video reads | The reply always arrives after **1011–1014 ms**, measured over hundreds of reads. It doesn't depend on the command. |
| Audio reads | About **11–12 ms** |
| Unknown or unsupported commands | **No reply at all.** No error frame and no NAK. |
| Recommended timeout | **2 s**. With a shorter timeout you'll retry while the first reply is still on its way, and replies then fall out of step with requests. |
| Duplicate replies | Sometimes the unit sends an identical copy of a reply about **1.5 s** later. Ignore exact duplicates. |
| Unsolicited frames | The unit sends frames on its own when something changes on the panel. See below. |

Consequences:

- **Match every reply to its request** (same family, EFF and CMD, ignoring the read/write bit; plus the channel or sub-address where there is one). Don't assume the next frame is the answer to the last thing you sent.
- Reading all 26 documented video settings takes about 26 s, because each read waits about 1 s. Audio is fast.
- 📄 Sony notes that a command takes about 5 ms to transmit, and advises not to send commands faster than the unit can act on them.

## Messages the switcher sends on its own ✅

The switcher reports some panel changes without being asked. These frames look exactly like replies.

| Frame (MSG part) | When |
|---|---|
| `03 04 00 80 40 nn` | M/E A-bus (program) source changed to button `nn` |
| `03 05 20 B7 00 00 01` | a transition has started (🔍 undocumented section `EFF 20`) |
| `03 05 20 B7 00 00 00` | the transition has finished |

❓ Whether Aux bus changes made on the panel are also reported hasn't been tested yet.

A controller should therefore treat any frame that arrives when no request is waiting as a **status update**, not as an error.
