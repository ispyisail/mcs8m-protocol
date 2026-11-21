# MCS-8M bridge

A small always-on service that **owns the switcher's serial port** and shares it over the network. Dashboards, Stream Deck / Companion, and (coming next) **vMix** and **OBS** can then all see and control the switcher at the same time.

```
                       ┌── vMix   (stage 2: follow / control)
MCS-8M ══ RS-232 ══ BRIDGE ──── OBS    (stage 3: follow / control)
                       ├── Companion / Stream Deck (simple HTTP, no hex)
                       └── dashboards, tally lights, scripts (HTTP + live events)
```

> **Status: stage 1 (core).** The state tracking, HTTP API, live events, video-loop guard and lead modes are done, and tested against the [simulator](../tools/simulator.py) at real switcher timing. The vMix and OBS connectors come next. Not yet tested on a real MCS-8M.

## Run it

```sh
cp bridge/config.example.json bridge/config.json     # then edit it
python3 bridge/mcs8m_bridge.py                        # Windows: py bridge\mcs8m_bridge.py
```

Open `http://<computer>:8765/` for a live status page.

Python 3.7+ with no extra libraries; on Windows, `py -m pip install pyserial`. **Nothing else can use the serial port while the bridge runs.** That includes Node-RED's serial nodes, and Companion's TCP Serial module.

## API

All commands are `POST` requests; `GET` is read-only.

| Request | Does |
|---|---|
| `GET /api/state` | Current state as JSON: program, preset, aux1, aux2, transition, muted, input names, apps, loop_warning |
| `GET /api/events` | Live changes ([Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)). The first message is the full state. |
| `POST /api/program/<n>` | Hot cut input *n* (1–14) to program |
| `POST /api/preset/<n>` | Select input *n* on preset |
| `POST /api/cut` | Cut preset to program |
| `POST /api/auto` · `POST /api/auto/<frames>` | AUTO TRANS (default 30 frames) |
| `POST /api/aux/<1\|2>/<n>` | Route input *n* to Aux 1 or Aux 2 |
| `POST /api/mute/<ch>/<on\|off>` | Mute (CH ON dark) or unmute channel 1–6 |
| `POST /api/mode/<app>/<mode>` | Who leads, for `vmix` or `obs` (see below) |

Replies: **202** sent · **409** refused, with the reason (bad value, or it would make a video loop) · **401** wrong token · **404** unknown command.

```sh
curl -X POST http://pi.local:8765/api/aux/1/3
curl http://pi.local:8765/api/state
curl -N http://pi.local:8765/api/events
```

```
data: {"type": "change", "key": "program", "value": 3, "old": 2, "name": "Camera 3", "origin": "switcher", "time": 1763712000.1}
```

Every change has an **`origin`**: `switcher` (made on its own panel), `api`, `vmix` or `obs`. Connectors use it to ignore their own echoes.

**Security:** set `http.token` in the config, and every POST must then send `Authorization: Bearer <token>` (or `?token=<token>`). Reading state doesn't need the token.

## Who leads

Each app (vMix, OBS) has its own mode, set in the config and changeable live with `POST /api/mode/<app>/<mode>`:

| Mode | Behaviour |
|---|---|
| `switcher_leads` (default) | You cut on the Sony; the app follows |
| `app_leads` | You cut in the app; the Sony follows |
| `both` | Whoever changed last wins. The bridge ignores the echo of changes it made itself, so they don't bounce back. |
| `off` | Watch only |

## Video-loop protection

vMix gets the Sony's program through a capture card and sends its output back into a Sony input. If the Sony puts that return on air **while** vMix is showing the capture input, the picture feeds back on itself. Set:

```json
"loop_protection": { "switcher_input_from_vmix": 8, "vmix_input_from_switcher": "1" }
```

- `switcher_input_from_vmix`: the Sony input that receives vMix's output.
- `vmix_input_from_switcher`: the vMix input with the capture card.

Then the bridge:
- **refuses** a cut or AUTO TRANS to the return input while vMix is showing the capture input. Putting it on preset is still allowed.
- **refuses** to switch vMix to the capture input while the Sony has the return on air.
- **warns** (`loop_warning` in the state, and a `loop` event) if someone creates a loop by hand anyway. The warning clears once the loop is broken.

## Tests

```sh
python3 bridge/test_bridge.py               # fast simulator
REAL_SPEED=1 python3 bridge/test_bridge.py  # real ~1 s video timing
```

They cover state tracking, every command, where each change came from, panel changes, the token, bad input, the loop guard (refused cut, allowed preset, refused AUTO TRANS, loop warning raised and cleared), and modes.
