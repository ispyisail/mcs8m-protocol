"""
obs.py - OBS Studio connector for the MCS-8M bridge (obs-websocket v5, built into
OBS 28+; Tools -> WebSocket Server Settings).

One map links Sony inputs and OBS scenes, e.g. {"1": "Camera 1", "2": "Camera 2"}.
Switcher leads: when the Sony's program changes, OBS switches to the matching scene.
OBS leads: when OBS's program scene changes, the Sony cuts to the matching input.
Both: either side, with echo suppression. Scenes not in the map are ignored.

Includes a minimal WebSocket client, so the bridge still needs only the standard library.
"""
import base64, hashlib, json, os, queue, socket, struct, threading, time

ECHO_SECONDS = 3.0
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def clean(d):
    return {k: v for k, v in (d or {}).items() if not str(k).startswith("_")}


def obs_auth(password, salt, challenge):
    """obs-websocket v5: base64(sha256(base64(sha256(password + salt)) + challenge))"""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


# ============================================================================ tiny WebSocket client
class WebSocket:
    def __init__(self, host, port, path="/", protocol="obswebsocket.json", timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: {protocol}\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("connection closed during handshake")
            head += chunk
        head, self.buf = head.split(b"\r\n\r\n", 1)
        lines = head.decode("latin1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise OSError(f"WebSocket upgrade refused: {lines[0]}")
        hdr = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
        expect = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if hdr.get("sec-websocket-accept") != expect:
            raise OSError("bad Sec-WebSocket-Accept")
        self.lock = threading.Lock()

    def send_text(self, text):
        self._send(0x1, text.encode())

    def _send(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack("!H", n) if n < 65536
                                                   else bytes([0x80 | 127]) + struct.pack("!Q", n))
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.lock:
            self.sock.sendall(head + mask + body)

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("connection closed")
            self.buf += chunk

    def recv_text(self, timeout):
        """Next text message, or None on timeout. Answers pings; raises OSError on close."""
        self.sock.settimeout(timeout)
        parts, end = [], time.monotonic() + timeout
        try:
            while True:
                self._need(2)
                b0, b1 = self.buf[0], self.buf[1]
                n, i = b1 & 0x7F, 2
                if n == 126:
                    self._need(4); n, i = struct.unpack("!H", self.buf[2:4])[0], 4
                elif n == 127:
                    self._need(10); n, i = struct.unpack("!Q", self.buf[2:10])[0], 10
                mlen = 4 if b1 & 0x80 else 0
                self._need(i + mlen + n)
                mask, data = self.buf[i:i + mlen], self.buf[i + mlen:i + mlen + n]
                self.buf = self.buf[i + mlen + n:]
                if mlen:
                    data = bytes(b ^ mask[k % 4] for k, b in enumerate(data))
                op = b0 & 0x0F
                if op == 0x8:
                    code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else 1005
                    raise OSError(f"closed by server (code {code} {data[2:].decode('utf-8', 'replace')})")
                if op == 0x9:
                    self._send(0xA, data); continue
                if op in (0x1, 0x0):
                    parts.append(data)
                    if b0 & 0x80:
                        return b"".join(parts).decode("utf-8")
                self.sock.settimeout(max(0.01, end - time.monotonic()))
        except socket.timeout:
            if parts:
                raise OSError("timed out mid-message")
            return None

    def close(self):
        try:
            self._send(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        self.sock.close()


# ============================================================================ connector
class ObsConnector:
    def __init__(self, bridge, cfg, log=print):
        self.b, self.log = bridge, log
        self.cfg = clean(cfg)
        self.host, self.port = self.cfg.get("host", "127.0.0.1"), int(self.cfg.get("port", 4455))
        self.password = self.cfg.get("password") or ""
        self.sony_to_scene = {int(k): v for k, v in clean(self.cfg.get("scenes")).items()}
        self.scene_to_sony = {}
        for k, v in self.sony_to_scene.items():
            self.scene_to_sony.setdefault(v, k)
        self.preview_follows = bool(self.cfg.get("preview_follows_preset", False))
        ctl = clean(self.cfg.get("control"))
        self.transition, self.auto_frames = ctl.get("transition", "cut"), int(ctl.get("auto_frames", 30))
        self.sendq, self.expected, self.req_id = queue.Queue(), [], 0
        self.program = self.preview = None
        self.studio = False
        bridge.register_app("obs", self.cfg.get("mode", "switcher_leads"))
        bridge.listeners.append(self.on_bridge_event)

    def mode(self):
        return self.b.apps["obs"]["mode"]

    def start(self):
        threading.Thread(target=self.run, name="obs", daemon=True).start()
        return self

    # ------------------------------------------------------------ Sony -> OBS
    def on_bridge_event(self, ev):
        if ev.get("type") != "change" or ev.get("value") is None or ev.get("origin") == "obs":
            return
        if self.mode() not in ("switcher_leads", "both"):
            return
        scene = self.sony_to_scene.get(ev["value"]) if isinstance(ev["value"], int) else None
        if not scene:
            return
        if ev.get("key") == "program" and scene != self.program:
            self.expected.append([scene, time.monotonic() + ECHO_SECONDS])
            self.request("SetCurrentProgramScene", sceneName=scene)
        elif ev.get("key") == "preset" and self.preview_follows and self.studio and scene != self.preview:
            self.request("SetCurrentPreviewScene", sceneName=scene)

    def sync_to_switcher(self):
        """On (re)connect: if OBS follows the Sony, bring it in line with what's on air now."""
        if self.mode() not in ("switcher_leads", "both"):
            return
        with self.b.lock:
            prog = self.b.state["program"]
        scene = self.sony_to_scene.get(prog)
        if scene and scene != self.program:
            self.expected.append([scene, time.monotonic() + ECHO_SECONDS])
            self.request("SetCurrentProgramScene", sceneName=scene)

    def request(self, rtype, **data):
        self.req_id += 1
        self.sendq.put({"op": 6, "d": {"requestType": rtype, "requestId": f"b{self.req_id}", "requestData": data}})

    # ------------------------------------------------------------ OBS -> Sony
    def on_obs_program(self, scene):
        changed = scene != self.program
        self.program = scene
        self.b.update_app("obs", program=scene)
        if not changed:
            return
        now = time.monotonic()
        self.expected = [e for e in self.expected if e[1] > now]
        mine = next((e for e in self.expected if e[0] == scene), None)
        if mine:
            self.expected.remove(mine)
            return                                          # the switch we asked for
        if self.mode() not in ("app_leads", "both"):
            return
        target = self.scene_to_sony.get(scene)
        if target is None:
            return
        try:
            if self.transition == "auto":
                self.b.preset(target, origin="obs")
                self.b.auto(self.auto_frames, origin="obs")
            else:
                self.b.program(target, origin="obs")
            self.log(f"obs: scene {scene!r} -> switcher input {target} ({self.transition})")
        except Exception as e:
            self.log(f"obs: {e}")
            self.b.emit({"type": "refused", "app": "obs", "error": str(e)})

    # ------------------------------------------------------------ connection
    def run(self):
        while not self.b.stop.is_set():
            ws = None
            try:
                ws = WebSocket(self.host, self.port)
                self.session(ws)
            except (OSError, ValueError, KeyError) as e:
                self.log(f"obs: {self.host}:{self.port} - {e} - retrying in 5 s")
            finally:
                if ws:
                    ws.close()
            self.b.update_app("obs", connected=False)
            self.b.stop.wait(5)

    def session(self, ws):
        hello = json.loads(ws.recv_text(5) or "{}")
        if hello.get("op") != 0:
            raise ValueError("no Hello from obs-websocket")
        ident = {"rpcVersion": 1}
        auth = hello["d"].get("authentication")
        if auth:
            if not self.password:
                raise ValueError("OBS requires a password - set obs.password in the config")
            ident["authentication"] = obs_auth(self.password, auth["salt"], auth["challenge"])
        ws.send_text(json.dumps({"op": 1, "d": ident}))
        msg = json.loads(ws.recv_text(5) or "{}")
        if msg.get("op") != 2:
            raise ValueError("OBS did not accept the connection (wrong password?)")
        self.log(f"obs: connected to {self.host}:{self.port}")
        self.b.update_app("obs", connected=True)
        self.request("GetStudioModeEnabled")
        self.request("GetCurrentProgramScene")
        while not self.b.stop.is_set():
            while True:
                try:
                    ws.send_text(json.dumps(self.sendq.get_nowait()))
                except queue.Empty:
                    break
            text = ws.recv_text(0.1)
            if text is None:
                continue
            m = json.loads(text)
            op, d = m.get("op"), m.get("d", {})
            if op == 5:                                       # event
                et, ed = d.get("eventType"), d.get("eventData", {})
                if et == "CurrentProgramSceneChanged":
                    self.on_obs_program(ed.get("sceneName"))
                elif et == "CurrentPreviewSceneChanged":
                    self.preview = ed.get("sceneName"); self.b.update_app("obs", preview=self.preview)
                elif et == "StudioModeStateChanged":
                    self.studio = bool(ed.get("studioModeEnabled"))
            elif op == 7:                                     # request response
                st, rd = d.get("requestStatus", {}), d.get("responseData") or {}
                if not st.get("result"):
                    self.log(f"obs: {d.get('requestType')} failed: {st.get('comment') or st.get('code')}")
                    self.b.emit({"type": "app_error", "app": "obs", "error": f"{d.get('requestType')}: {st.get('comment')}"})
                elif d.get("requestType") == "GetCurrentProgramScene":
                    self.program = rd.get("sceneName") or rd.get("currentProgramSceneName")
                    self.b.update_app("obs", program=self.program)
                    self.sync_to_switcher()
                elif d.get("requestType") == "GetStudioModeEnabled":
                    self.studio = bool(rd.get("studioModeEnabled"))
