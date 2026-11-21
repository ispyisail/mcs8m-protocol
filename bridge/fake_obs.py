#!/usr/bin/env python3
"""
fake_obs.py - a stand-in for OBS's obs-websocket v5 server, for testing the bridge
without OBS. Real WebSocket framing and v5 authentication (same algorithm as
obs-websocket's Utils::Crypto), Hello/Identify/Identified, the scene requests the
bridge uses, and CurrentProgramScene/PreviewScene events.

    python3 bridge/fake_obs.py [--port 4455] [--password secret]    then: scene NAME | quit
"""
import argparse, base64, hashlib, json, os, socket, struct, sys, threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeObs:
    def __init__(self, port=4455, password="", scenes=("Starting Soon", "Camera 1", "Camera 2", "Camera 3", "Camera 4", "Slides"),
                 studio=False):
        self.password, self.scenes, self.studio = password, list(scenes), studio
        self.program, self.preview = self.scenes[0], self.scenes[0]
        self.requests, self.clients = [], []
        self.lock = threading.Lock()
        self.srv = socket.create_server(("127.0.0.1", port))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    # ---------------------------------------------------------------- websocket
    @staticmethod
    def _send(c, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n))
        c.sendall(head + payload)

    def send(self, c, obj):
        self._send(c, 0x1, json.dumps(obj).encode())

    @staticmethod
    def _recv(c, buf):
        def need(n):
            nonlocal buf
            while len(buf) < n:
                d = c.recv(65536)
                if not d:
                    raise OSError("closed")
                buf += d
        need(2)
        op, n, i = buf[0] & 0x0F, buf[1] & 0x7F, 2
        if n == 126:
            need(4); n, i = struct.unpack("!H", buf[2:4])[0], 4
        need(i + 4 + n)
        if not buf[1] & 0x80:
            raise OSError("client frame not masked")                  # RFC 6455: clients must mask
        mask, data = buf[i:i + 4], bytes(b ^ buf[i + (k % 4)] for k, b in enumerate(buf[i + 4:i + 4 + n]))
        return op, data, buf[i + 4 + n:]

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _client(self, c):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                head += c.recv(4096)
            head, buf = head.split(b"\r\n\r\n", 1)
            hdr = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip()
                   for l in head.decode().split("\r\n")[1:] if ":" in l}
            accept = base64.b64encode(hashlib.sha1((hdr["sec-websocket-key"] + GUID).encode()).digest()).decode()
            c.sendall((f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
            hello = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
            salt = challenge = None
            if self.password:
                salt, challenge = base64.b64encode(os.urandom(32)).decode(), base64.b64encode(os.urandom(32)).decode()
                hello["authentication"] = {"challenge": challenge, "salt": salt}
            self.send(c, {"op": 0, "d": hello})
            identified = False
            while True:
                op, data, buf = self._recv(c, buf)
                if op == 0x8:
                    break
                if op != 0x1:
                    continue
                m = json.loads(data)
                if m["op"] == 1:
                    if self.password:
                        # same steps as obs-websocket's Utils::Crypto::GenerateSecret / CheckAuthenticationString
                        secret = base64.b64encode(hashlib.sha256(self.password.encode() + salt.encode()).digest()).decode()
                        good = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
                        if m["d"].get("authentication") != good:
                            self._send(c, 0x8, struct.pack("!H", 4009) + b"Authentication failed.")
                            break
                    identified = True
                    with self.lock:
                        self.clients.append(c)
                    self.send(c, {"op": 2, "d": {"negotiatedRpcVersion": 1}})
                elif m["op"] == 6 and identified:
                    self.send(c, {"op": 7, "d": self.request(m["d"])})
        except (OSError, KeyError, ValueError):
            pass
        finally:
            with self.lock:
                if c in self.clients:
                    self.clients.remove(c)
            c.close()

    # ---------------------------------------------------------------- requests and events
    def request(self, d):
        rt, rd = d["requestType"], d.get("requestData") or {}
        self.requests.append((rt, rd))
        ok = lambda data=None: {"requestType": rt, "requestId": d["requestId"],
                                "requestStatus": {"result": True, "code": 100}, **({"responseData": data} if data else {})}
        err = lambda code, comment: {"requestType": rt, "requestId": d["requestId"],
                                     "requestStatus": {"result": False, "code": code, "comment": comment}}
        if rt == "GetCurrentProgramScene":
            return ok({"sceneName": self.program, "currentProgramSceneName": self.program})
        if rt == "GetStudioModeEnabled":
            return ok({"studioModeEnabled": self.studio})
        if rt in ("SetCurrentProgramScene", "SetCurrentPreviewScene"):
            name = rd.get("sceneName")
            if name not in self.scenes:
                return err(600, f"No source was found by the name of `{name}`.")
            if rt == "SetCurrentPreviewScene":
                if not self.studio:
                    return err(506, "Studio mode is not active.")
                self.preview = name
                self.event("CurrentPreviewSceneChanged", {"sceneName": name})
            else:
                self.switch(name)
            return ok()
        return err(204, f"Unknown request type `{rt}`.")

    def event(self, etype, data):
        for c in list(self.clients):
            try:
                self.send(c, {"op": 5, "d": {"eventType": etype, "eventIntent": 4, "eventData": data}})
            except OSError:
                pass

    def switch(self, name):
        """Program scene change (from a request, or the OBS operator clicking a scene)."""
        if name != self.program:
            self.program = name
            self.event("CurrentProgramSceneChanged", {"sceneName": name, "sceneUuid": "uuid-" + name})

    def close(self):
        try:
            self.srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.srv.close()
        for c in list(self.clients):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=4455); ap.add_argument("--password", default="")
    a = ap.parse_args()
    o = FakeObs(a.port, a.password)
    print(f"fake OBS on ws://127.0.0.1:{o.port}  scenes: {', '.join(o.scenes)}   (scene NAME | quit)", flush=True)
    for line in sys.stdin:
        if line.startswith("scene "):
            o.switch(line[6:].strip())
        elif line.strip() == "quit":
            break
