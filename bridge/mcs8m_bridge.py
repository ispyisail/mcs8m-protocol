#!/usr/bin/env python3
"""
mcs8m_bridge.py - network bridge for the Sony MCS-8M.

Owns the switcher's serial port, keeps its live state (program, preset, Aux 1/2,
transition, mutes), and offers it to everything else over HTTP:

    GET  /api/state                     current state (JSON)
    GET  /api/events                    live changes (Server-Sent Events)
    POST /api/program/<n>               hot cut input n to program
    POST /api/preset/<n>                select input n on preset
    POST /api/cut                       cut preset to program
    POST /api/auto[/<frames>]           AUTO TRANS (default 30 frames)
    POST /api/aux/<1|2>/<n>             route input n to Aux 1 or 2
    POST /api/mute/<ch>/<on|off>        mute (on = CH ON dark) channel 1-6
    POST /api/mode/<app>/<mode>         who leads: switcher_leads | app_leads | both | off

Every change carries an "origin" (switcher = its panel, api, vmix, obs), which is
what lets connectors avoid echo loops. A video-loop guard refuses changes that
would put the vMix return on air while vMix shows the switcher (see README).

Connectors: vmix.py (vMix TCP API), obs.py (obs-websocket v5). Python 3.7+ standard library only (Windows: pip install pyserial).
    python3 bridge/mcs8m_bridge.py -c bridge/config.json
"""
import argparse, hmac, json, os, queue, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "examples", "python"))
import mcs8m as M                                                   # noqa: E402

VERSION = "0.1.0"
MODES = ("switcher_leads", "app_leads", "both", "off")
ECHO_SECONDS = 3.0          # how long a change we caused is recognised as ours


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Refused(Exception):
    """A request the bridge won't carry out (bad input, or it would cause a video loop)."""


def bcd3(frames):
    return [frames // 100, ((frames // 10 % 10) << 4) | (frames % 10)]


# ============================================================================ core
class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.names = {str(k): v for k, v in cfg.get("inputs", {}).items()}
        self.lock = threading.RLock()
        self.state = {"connected": False, "program": None, "preset": None, "aux1": None, "aux2": None,
                      "transition": False, "muted": {str(c): None for c in range(1, 7)}}
        self.apps = {}                  # name -> {"mode", "connected", "program", "preview"}
        self.loop_warning = None
        self.listeners = []             # callables(event) - used by connectors
        self.streams = []               # queue.Queue per SSE client
        self.cmdq = queue.Queue()
        self.expected = []              # [key, value, origin, deadline]
        self.stop = threading.Event()
        self.last_rx = 0.0
        self.last_change = {}           # key -> monotonic time its value last changed
        self.pending_reads = []         # [key, sent_time, value_at_send] for poll reads in flight
        self.reread = []                # keys to read again (after a stale reply)

    # ---------------------------------------------------------------- apps (connectors)
    def register_app(self, name, mode):
        if mode not in MODES:
            raise ValueError(f"{name}: mode must be one of {MODES}")
        with self.lock:
            self.apps[name] = {"mode": mode, "connected": False, "program": None, "preview": None}
        self.emit({"type": "app", "app": name, **self.apps[name]})

    def set_mode(self, name, mode):
        if name not in self.apps:
            raise Refused(f"unknown app {name!r} (enabled: {', '.join(self.apps) or 'none'})")
        if mode not in MODES:
            raise Refused(f"mode must be one of: {', '.join(MODES)}")
        with self.lock:
            self.apps[name]["mode"] = mode
        log(f"mode {name} = {mode}")
        self.emit({"type": "app", "app": name, **self.apps[name]})

    def update_app(self, name, **fields):
        """Connectors report their side's state here (connected, program, preview)."""
        with self.lock:
            app = self.apps[name]
            changed = {k: v for k, v in fields.items() if app.get(k) != v}
            app.update(changed)
        if changed:
            self.emit({"type": "app", "app": name, **self.apps[name]})
            self.check_loop()

    # ---------------------------------------------------------------- events
    def snapshot(self):
        with self.lock:
            return {"bridge": VERSION, **json.loads(json.dumps(self.state)),
                    "names": self.names, "apps": json.loads(json.dumps(self.apps)),
                    "loop_warning": self.loop_warning}

    def emit(self, ev):
        ev = {"time": time.time(), **ev}
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception as e:                                  # a broken connector must not stop the bridge
                log(f"listener error: {e!r}")
        for q in list(self.streams):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    def expect(self, key, value, origin):
        with self.lock:
            self.expected.append([key, value, origin, time.monotonic() + ECHO_SECONDS])

    def _origin(self, key, value):
        now = time.monotonic()
        self.expected = [e for e in self.expected if e[3] > now]
        for e in self.expected:
            if e[0] == key and e[1] == value:
                self.expected.remove(e)
                return e[2]
        return "switcher"

    def set(self, key, value):
        """Record a value reported by the switcher; emit a change event if it changed."""
        with self.lock:
            if key.startswith("muted."):
                ch = key.split(".")[1]
                old = self.state["muted"][ch]
                if old == value:
                    return
                self.state["muted"][ch] = value
            else:
                old = self.state.get(key)
                if old == value:
                    return
                self.state[key] = value
            origin = self._origin(key, value)
            self.last_change[key] = time.monotonic()
        ev = {"type": "change", "key": key, "value": value, "old": old, "origin": origin}
        if key in ("program", "preset", "aux1", "aux2") and value is not None:
            ev["name"] = self.name(value)
        log(f"{key} = {value}" + (f" ({ev['name']})" if "name" in ev else "") + f"  [{origin}]")
        self.emit(ev)
        if key == "program":
            self.check_loop()

    def name(self, n):
        return self.names.get(str(n), f"Input {n}")

    # ---------------------------------------------------------------- video-loop guard
    def _loop_cfg(self):
        lp = self.cfg.get("loop_protection") or {}
        return lp.get("switcher_input_from_vmix"), lp.get("vmix_input_from_switcher")

    def would_loop(self, switcher_program=None, vmix_program=None):
        """True if this combination puts the vMix return on air while vMix shows the switcher."""
        ret, cap = self._loop_cfg()
        if ret is None or cap is None:
            return False
        with self.lock:
            sp = self.state["program"] if switcher_program is None else switcher_program
            vp = (self.apps.get("vmix") or {}).get("program") if vmix_program is None else vmix_program
        return sp is not None and vp is not None and int(sp) == int(ret) and str(vp) == str(cap)

    def guard_switcher(self, new_program):
        if self.would_loop(switcher_program=new_program):
            ret, cap = self._loop_cfg()
            raise Refused(f"refused: input {ret} is the vMix return and vMix is showing input {cap} "
                          f"(the switcher's own output) - that would make a video loop")

    def guard_vmix(self, new_vmix_program):
        if self.would_loop(vmix_program=new_vmix_program):
            ret, cap = self._loop_cfg()
            raise Refused(f"refused: vMix input {cap} is the switcher's output and the switcher has the "
                          f"vMix return (input {ret}) on air - that would make a video loop")

    def check_loop(self):
        warn = None
        if self.would_loop():
            ret, cap = self._loop_cfg()
            warn = (f"VIDEO LOOP: the switcher has the vMix return (input {ret}) on air "
                    f"while vMix is showing the switcher (vMix input {cap})")
        if warn != self.loop_warning:
            self.loop_warning = warn
            if warn:
                log("WARNING " + warn)
            self.emit({"type": "loop", "warning": warn})

    # ---------------------------------------------------------------- commands
    def _input(self, n):
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise Refused(f"input must be a number 1-14, got {n!r}")
        if not 1 <= n <= 14:
            raise Refused(f"input must be 1-14, got {n}")
        return n

    def _send(self, frame, after=()):
        self.cmdq.put((frame, list(after)))

    def program(self, n, origin="api"):
        n = self._input(n)
        self.guard_switcher(n)
        self.expect("program", n, origin)
        self._send(M.build_frame(M.VIDEO, M.video_msg(M.ME, 0x80, 0x00, n)))

    def preset(self, n, origin="api"):
        n = self._input(n)
        self.expect("preset", n, origin)
        self._send(M.build_frame(M.VIDEO, M.video_msg(M.ME, 0x81, 0x00, n)))

    def auto(self, frames=30, origin="api"):
        try:
            frames = int(frames)
        except (TypeError, ValueError):
            raise Refused("frames must be a number")
        if not 0 <= frames <= 999:
            raise Refused("frames must be 0-999")
        with self.lock:
            prog, pre = self.state["program"], self.state["preset"]
        if pre is not None:
            self.guard_switcher(pre)
            self.expect("program", pre, origin)
            if prog is not None:
                self.expect("preset", prog, origin)
        self._send(M.build_frame(M.VIDEO, M.video_msg(M.ME, 0x96, 0x00, *bcd3(frames))),
                   after=[self._read_video(M.ME, M.BUS_A), self._read_video(M.ME, M.BUS_B)])

    def cut(self, origin="api"):
        self.auto(0, origin)

    def aux(self, a, n, origin="api"):
        if str(a) not in ("1", "2"):
            raise Refused("aux must be 1 or 2")
        n = self._input(n)
        self.expect(f"aux{a}", n, origin)
        self._send(M.build_frame(M.VIDEO, M.video_msg(M.AUX1 if str(a) == "1" else M.AUX2, 0x80, 0x00, n)))

    def mute(self, ch, on, origin="api"):
        try:
            ch = int(ch)
        except (TypeError, ValueError):
            raise Refused("channel must be 1-6")
        if not 1 <= ch <= 6:
            raise Refused("channel must be 1-6")
        self.expect(f"muted.{ch}", bool(on), origin)
        # audio writes are only ACKed, so read the value back to confirm
        self._send(M.build_frame(M.AUDIO, M.audio_msg(0x13, ch - 1, 1 if on else 0)),
                   after=[M.build_frame(M.AUDIO, M.audio_msg(0x93, ch - 1, 0))])

    @staticmethod
    def _read_video(eff, cmd):
        return M.build_frame(M.VIDEO, M.video_msg(eff, cmd))

    def read_frame_for(self, key):
        rev = {v: k for k, v in self.XPT_KEYS.items()}
        if key in rev:
            return self._read_video(*rev[key])
        if key.startswith("muted."):
            return M.build_frame(M.AUDIO, M.audio_msg(0x93, int(key.split(".")[1]) - 1, 0))
        return None

    def _note_read(self, frame, now):
        msg = frame[9:-2]
        if frame[4] == M.VIDEO[0]:
            key = self.XPT_KEYS.get((msg[2], msg[3]))
        else:
            key = f"muted.{msg[1] + 1}" if msg[0] == 0x93 else None
        if key:
            with self.lock:
                self.pending_reads.append([key, now, self._current(key)])

    def poll_frames(self):
        v = self._read_video
        return [v(M.ME, M.BUS_A), v(M.ME, M.BUS_B), v(M.AUX1, M.BUS_A), v(M.AUX2, M.BUS_A)] + \
               [M.build_frame(M.AUDIO, M.audio_msg(0x93, ch, 0)) for ch in range(6)]

    # ---------------------------------------------------------------- serial worker
    XPT_KEYS = {(M.ME, M.BUS_A): "program", (M.ME, M.BUS_B): "preset", (M.AUX1, M.BUS_A): "aux1", (M.AUX2, M.BUS_A): "aux2"}

    def _key_value(self, kind, eff, cmd, data):
        if kind == "video" and len(data) >= 2 and (eff, cmd) in self.XPT_KEYS:
            return self.XPT_KEYS[(eff, cmd)], data[-1]
        if kind == "video" and eff == 0x20 and cmd == 0x37 and len(data) >= 3:
            return "transition", data[2] == 1
        if kind == "audio" and cmd == 0x13 and len(data) >= 2 and data[0] < 6:
            return f"muted.{data[0] + 1}", data[1] == 1
        return None, None

    def _current(self, key):
        return self.state["muted"][key.split(".")[1]] if key.startswith("muted.") else self.state.get(key)

    def receive(self, f):
        """Turn any frame from the switcher (reply, answer to a write, or push) into state."""
        now = self.last_rx = time.monotonic()
        key, value = self._key_value(f.kind, f.eff, f.cmd, f.data)
        if key is None:
            return
        with self.lock:
            self.pending_reads = [p for p in self.pending_reads if now - p[1] < 2.5]
            # A poll read takes ~1 s to answer. If the value changed after the read was sent (a panel
            # change, or a command), a reply carrying the OLD value is stale: ignore it and read again.
            stale = next((p for p in self.pending_reads if p[0] == key and self.last_change.get(key, 0) > p[1]
                          and value == p[2] and value != self._current(key)), None)
            if stale:
                self.pending_reads.remove(stale)
                self.reread.append(key)
                return
            mine = next((p for p in self.pending_reads if p[0] == key), None)
            if mine and value == self._current(key):
                self.pending_reads.remove(mine)
        self.set(key, value)

    @staticmethod
    def _same_request(req, f):
        msg = req[9:-2]
        if req[4] == M.VIDEO[0]:
            return f.kind == "video" and f.eff == msg[2] and f.cmd == (msg[3] & 0x7F)
        return f.kind == "audio" and f.cmd == (msg[0] & 0x7F) and f.data[:1] == msg[1:2]

    def run_serial(self):
        port_name = self.cfg.get("serial_port", "/dev/ttyUSB0")
        poll_every = float(self.cfg.get("poll_seconds", 10))
        while not self.stop.is_set():
            try:
                port = M.SerialPort(port_name)
            except (OSError, SystemExit) as e:
                self._connected(False)
                log(f"can't open {port_name}: {e} - retrying in 5 s")
                self.stop.wait(5)
                continue
            log(f"serial port {port_name} open")
            parser, todo, outstanding, next_poll = M.FrameParser(), [], None, 0.0
            try:
                while not self.stop.is_set():
                    while True:                                     # commands first: they're what people wait for
                        try:
                            frame, after = self.cmdq.get_nowait()
                        except queue.Empty:
                            break
                        port.write(frame)
                        todo[:0] = after
                    now = time.monotonic()
                    if now >= next_poll and not todo:
                        todo, next_poll = self.poll_frames(), now + poll_every
                    with self.lock:
                        while self.reread:
                            frame = self.read_frame_for(self.reread.pop(0))
                            if frame and frame not in todo:
                                todo.insert(0, frame)
                    if todo and (outstanding is None or now > outstanding[1]):
                        f = todo.pop(0)                             # one read in flight at a time
                        self._note_read(f, now)
                        port.write(f)
                        outstanding = (f, now + (1.6 if f[4] == M.VIDEO[0] else 0.3))
                    for fr in parser.feed(port.read(0.05)):
                        self.receive(fr)
                        if outstanding and self._same_request(outstanding[0], fr):
                            outstanding = None
                    self._connected(time.monotonic() - self.last_rx < poll_every * 2 + 5)
            except OSError as e:
                log(f"serial error: {e} - reopening")
            finally:
                try:
                    port.close()
                except OSError:
                    pass
            self._connected(False)
            self.stop.wait(2)

    def _connected(self, ok):
        with self.lock:
            changed = self.state["connected"] != ok
            self.state["connected"] = ok
        if changed:
            log("switcher " + ("CONNECTED" if ok else "NOT RESPONDING"))
            self.emit({"type": "change", "key": "connected", "value": ok, "origin": "bridge"})


# ============================================================================ HTTP
STATUS_PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>MCS-8M bridge</title><style>body{font:16px system-ui;margin:1em}pre{background:#f4f4f4;padding:1em}</style>
<h1>MCS-8M bridge</h1><p id=w style="color:#b00;font-weight:bold"></p><pre id=s>connecting...</pre>
<script>
function show(){fetch('api/state').then(r=>r.json()).then(s=>{document.getElementById('s').textContent=JSON.stringify(s,null,1);
document.getElementById('w').textContent=s.loop_warning||''})}
show(); new EventSource('api/events').onmessage=show;
</script>"""


def make_handler(bridge):
    token = (bridge.cfg.get("http") or {}).get("token") or ""

    class Handler(BaseHTTPRequestHandler):
        server_version = "mcs8m-bridge/" + VERSION

        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self, query):
            if not token:
                return True
            auth = self.headers.get("Authorization", "")
            given = (auth[7:] if auth.startswith("Bearer ") else auth).strip() or (query.get("token") or [""])[0]
            return hmac.compare_digest(given, token)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                body = STATUS_PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/state":
                self._json(200, bridge.snapshot())
            elif u.path == "/api/events":
                self._events()
            else:
                self._json(404, {"error": "not found"})

        def _events(self):
            q = queue.Queue(maxsize=500)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            bridge.streams.append(q)
            try:
                self.wfile.write(f"event: state\ndata: {json.dumps(bridge.snapshot())}\n\n".encode())
                self.wfile.flush()
                while not bridge.stop.is_set():
                    try:
                        ev = q.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bridge.streams.remove(q)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.end_headers()

        def do_POST(self):
            u = urlparse(self.path)
            if not self._authorised(parse_qs(u.query)):
                return self._json(401, {"error": "missing or wrong token"})
            p = [x for x in u.path.split("/") if x]
            try:
                if p[:1] != ["api"]:
                    raise KeyError
                cmd, args = p[1], p[2:]
                if cmd == "program" and len(args) == 1:
                    bridge.program(args[0])
                elif cmd == "preset" and len(args) == 1:
                    bridge.preset(args[0])
                elif cmd == "cut" and not args:
                    bridge.cut()
                elif cmd == "auto" and len(args) <= 1:
                    bridge.auto(*(args or [30]))
                elif cmd == "aux" and len(args) == 2:
                    bridge.aux(args[0], args[1])
                elif cmd == "mute" and len(args) == 2 and args[1] in ("on", "off"):
                    bridge.mute(args[0], args[1] == "on")
                elif cmd == "mode" and len(args) == 2:
                    bridge.set_mode(args[0], args[1])
                else:
                    raise KeyError
            except (KeyError, IndexError):
                return self._json(404, {"error": "unknown command", "path": u.path})
            except Refused as e:
                return self._json(409, {"error": str(e)})
            self._json(202, {"ok": True, "sent": u.path})

    return Handler


# ============================================================================ main
def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def start(cfg, http=True):
    """Start the bridge (and its HTTP server). Returns (bridge, server)."""
    b = Bridge(cfg)
    vm = {k: v for k, v in (cfg.get("vmix") or {}).items() if not k.startswith("_")}
    if vm.get("enabled"):
        import vmix
        vmix.VmixConnector(b, vm, log=lambda *a: log(*a)).start()
    ob = {k: v for k, v in (cfg.get("obs") or {}).items() if not k.startswith("_")}
    if ob.get("enabled"):
        import obs
        obs.ObsConnector(b, ob, log=lambda *a: log(*a)).start()
    threading.Thread(target=b.run_serial, name="serial", daemon=True).start()
    srv = None
    if http:
        h = cfg.get("http") or {}
        srv = ThreadingHTTPServer((h.get("host", "0.0.0.0"), int(h.get("port", 8765))), make_handler(b))
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
        log(f"HTTP on http://{h.get('host', '0.0.0.0')}:{srv.server_address[1]}/")
    return b, srv


def main():
    ap = argparse.ArgumentParser(description="Network bridge for the Sony MCS-8M")
    ap.add_argument("-c", "--config", default=os.path.join(HERE, "config.json"))
    a = ap.parse_args()
    if not os.path.exists(a.config):
        sys.exit(f"no config at {a.config} - copy bridge/config.example.json to bridge/config.json and edit it")
    cfg = load_config(a.config)
    log(f"mcs8m-bridge {VERSION}")
    b, _ = start(cfg)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        b.stop.set()


if __name__ == "__main__":
    main()
