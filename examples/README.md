# Examples

Each example takes the serial port as an argument, and defaults to `/dev/ttyUSB0`. To try them without a switcher, start `tools/simulator.py` and pass the `/dev/pts/N` path it prints. See [Getting started](../docs/00-getting-started.md).

## Python (no dependencies)

| File | What it does | Changes the switcher? |
|---|---|---|
| [`python/mcs8m.py`](python/mcs8m.py) | Small library: frame building, BC parsing, reply matching, duplicate filtering, helpers | only if `allow_writes=True` |
| [`python/01_read_program.py`](python/01_read_program.py) | Prints program, preset, Aux 1 and Aux 2 | no |
| [`python/02_read_audio.py`](python/02_read_audio.py) | Prints every channel's fader in dB and its mute state | no |
| [`python/03_tally.py`](python/03_tally.py) | Live on-air display, using the switcher's push messages plus a slow poll | no |
| [`python/04_switch_aux.py`](python/04_switch_aux.py) | Routes a source to an Aux output and reads it back | **yes**; needs `--yes` |

```python
from mcs8m import MCS8M, ME, BUS_A

with MCS8M("/dev/ttyUSB0") as sw:
    print("on air:", sw.program())                 # ~1 s per video read
    print("CH6 muted:", sw.muted(5))               # channels are 0-5
    print("raw read:", sw.read_video(ME, 0x18, 0x00, 0x00).hex(" "))   # transition rate
    for event in sw.listen(10):                    # panel changes for 10 s
        print(event)
```

## Node.js

| File | What it does |
|---|---|
| [`javascript/mcs8m.js`](javascript/mcs8m.js) | Frame builders (`videoRead`, `audioRead`, `videoWrite`), `FrameParser`, `describe()`. No dependencies. |
| [`javascript/read_program.js`](javascript/read_program.js) | Reads program, preset and CH6 mute, then prints everything the switcher sends. Needs `npm install serialport`. |
| [`javascript/selftest.js`](javascript/selftest.js) | Offline check against captured frames: `node selftest.js` |

## Node-RED

### Ready-made dashboard

**[`node-red/dashboard/`](node-red/dashboard/)** is a complete phone and tablet control panel. It has Aux 1 and Aux 2 source buttons with LEDs, shows changes made on the switcher's own panel almost at once, and includes a connection watchdog, a QR code and a read-only DOWNLOAD ALL SETTINGS button. It has been used on a real switcher. **Start here if you just want projector control.**

### Minimal flow (for building your own)

[`node-red/mcs8m-example-flow.json`](node-red/mcs8m-example-flow.json) is a minimal flow:

```
[Read program] ┐
[Read preset ] ├─► Build request ─► 1 request / 1.2 s ─► serial out
[Read faders ] ┘
serial in ─► Parse MCS-8M frames ─► debug        (msg.topic = program / preset / aux1 /
                                                  mute.ch1 / faders / transition …)
```

1. Install **node-red-node-serialport** (Menu → Manage palette).
2. Menu → Import → paste the file's contents.
3. Open the **MCS-8M port** config node and set your serial port.
4. Deploy, then click the inject buttons. Changes made on the switcher's panel appear in the debug sidebar on their own.

The parser splits frames by byte count, so it works whatever way the serial-in node chops up the data. It outputs one message per frame, with `msg.topic` and `msg.payload.value`, ready to wire into dashboard nodes.

The **WRITE: Aux 1 = source 3** inject changes the output. Delete it if you only want monitoring.
