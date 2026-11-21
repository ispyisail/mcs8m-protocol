# 0. Getting started

From an unconnected switcher to reading what's on air, in about 15 minutes.

## What you need

- A Sony MCS-8M
- A computer with a serial port, or a **USB-to-RS-232 adapter** (FTDI- or Prolific-based ones are fine). A Raspberry Pi works well.
- A **null-modem (crossed) cable** with 9-pin D-sub connectors. A straight-through cable won't work. Check whether you need pins or sockets on each end; see [Hardware](07-hardware.md).
- Python 3 (built into Linux, macOS and Raspberry Pi OS)

No switcher yet? Skip to [Try it without hardware](#try-it-without-hardware).

## Step 1: enable serial control on the switcher

> 🛑 **This step is required.** Serial control is **off** from the factory. If it's off, the switcher silently ignores everything sent to it: no error, no reply, just like a broken cable.

1. On the switcher, open **Setup → System**.
2. Move the cursor to the bottom item, **Back**, and press the **V3** knob. A hidden **For Services** menu appears.
3. Set **Serial Cmd Group** to **Rem 1**.
4. **Save and restart with Startup Define.** If you skip this, the setting may not stick.
5. After the restart, check that Serial Cmd Group still says **Rem 1**.

> ⚠️ Change nothing else in For Services, and never choose **FACT**. That's a factory mode.

More detail, including "it worked yesterday but not today", is in [Hardware → Enable serial control](07-hardware.md#required-enable-serial-control-on-the-switcher).

## Step 2: connect

Plug the null-modem cable into the **REMOTE** connector on the back of the switcher, and the other end into your adapter. Then find the port name:

```sh
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null      # Linux / Raspberry Pi: usually /dev/ttyUSB0
ls /dev/cu.*                                  # macOS: e.g. /dev/cu.usbserial-1420
```

On Linux, if you get "Permission denied", add yourself to the `dialout` group and log in again:

```sh
sudo usermod -aG dialout $USER
```

Only one program can use the port at a time. **Stop anything else using it, Node-RED for example, before running these tools.**

## Step 3: get the code

```sh
git clone https://github.com/ispyisail/mcs8m-protocol.git
cd mcs8m-protocol
python3 tools/mcs8m_dump.py --selftest       # checks the frame builder: should print OK lines
```

## Step 4: first read

```sh
python3 examples/python/01_read_program.py /dev/ttyUSB0
```

```
Program (on air): 2
Preset  (next):   7
Aux 1:            7
Aux 2:            7
```

This takes about 4 seconds. **Each video read takes about 1 second to answer.** That's how the switcher behaves, not a fault.

Next:

```sh
python3 examples/python/02_read_audio.py /dev/ttyUSB0       # faders and mutes (fast)
python3 examples/python/03_tally.py /dev/ttyUSB0            # live on-air display, Ctrl+C to stop
python3 tools/mcs8m_dump.py -p /dev/ttyUSB0 -o settings.json  # every documented setting, ~31 s
```

## If nothing answers

| Symptom | Check |
|---|---|
| `NoReply` / "Nothing answered" | **First check:** is Serial Cmd Group set to **Rem 1**, and was it saved with **Startup Define**? A setting that wasn't saved is lost at power-off. |
| | Run the [loopback test](07-hardware.md#1-the-adapter-loopback-test) to rule out the adapter and cable. |
| | Is it a **null-modem** cable? Try a null-modem adapter. |
| | Is another program (e.g. Node-RED) holding the port? |
| | Is the port name right? Unplug the adapter, run `ls` again, and see which name disappears. |
| `Permission denied` | Add yourself to the `dialout` group (step 2). |
| Some reads time out | Use a timeout of at least **1.5 s**; 2 s is recommended. Video replies take ~1.01 s. |
| Your own code gets no reply at all | Is BC **big-endian**? The manual is wrong about this; see [errata](04-errata.md). |
| Replies seem to belong to the wrong request | Match replies to requests, and don't split the stream on `03`. See [framing](01-framing.md#reading-the-incoming-data). |

## Try it without hardware

`tools/simulator.py` pretends to be an MCS-8M on a virtual serial port. It copies the 1-second video delay, ACKs, duplicate replies and panel pushes.

```sh
python3 tools/simulator.py             # prints e.g.  Simulator on /dev/pts/5
```

In a second terminal:

```sh
python3 examples/python/03_tally.py /dev/pts/5
```

Back in the simulator's terminal, type panel actions and watch the tally react:

```
p 4      select button 4 on program
v 5      select button 5 on preset
t        AUTO TRANS
s        show the simulated state
```

Add `--fast` for quick replies while developing, and `--duplicates` to test that your code copes with repeated replies.

## Writing your own code

- **Python:** copy [`examples/python/mcs8m.py`](../examples/python/mcs8m.py). It has no dependencies.
  ```python
  from mcs8m import MCS8M
  with MCS8M("/dev/ttyUSB0") as sw:
      print(sw.program(), sw.faders(), sw.muted(5))
  ```
- **Node.js:** [`examples/javascript/mcs8m.js`](../examples/javascript/mcs8m.js) builds and parses frames; pair it with the `serialport` package.
- **Node-RED:** import [`examples/node-red/mcs8m-example-flow.json`](../examples/node-red/mcs8m-example-flow.json).

The four rules that catch everyone are explained in [01-framing.md](01-framing.md):

1. BC is **big-endian**.
2. Frame by **BC**, never by `03`.
3. The read/write bit is **reversed** between video and audio.
4. Video replies take **~1 s**, so match replies to requests.

## Safety

Every tool and example here is **read-only**, except `04_switch_aux.py` and the WRITE inject in the Node-RED flow. Those change a live output and are clearly marked. The Python library refuses writes unless you open it with `allow_writes=True`.

Be most careful with video section `EFF 21`: CMD `80` there overwrites a snapshot register.
