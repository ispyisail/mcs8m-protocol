#!/usr/bin/env python3
"""
fake_vmix.py - a stand-in for vMix's TCP API (port 8099), for testing the bridge
without vMix.  Implements what the bridge uses: SUBSCRIBE TALLY, TALLY, XML, and
FUNCTION CutDirect / PreviewInput / SetText / OverlayInputNIn / OverlayInputNOut.
Inputs can be referenced by number or title, as in vMix.

    python3 bridge/fake_vmix.py [--port 8099]      then type:  cut N | preview N | show | quit
"""
import argparse, socket, sys, threading
from urllib.parse import parse_qs


class FakeVmix:
    def __init__(self, port=8099, inputs=("MCS-8M capture", "Camera PiP", "Lower Third", "Blank", "Video", "Slides", "Graphics")):
        self.inputs = list(inputs)
        self.active, self.preview = 1, 2
        self.overlays, self.texts, self.calls = {}, {}, []
        self.lock, self.clients, self.subs = threading.Lock(), [], set()
        self.srv = socket.create_server(("127.0.0.1", port))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    # ---------------------------------------------------------------- state
    def resolve(self, ref):
        ref = str(ref)
        if ref.isdigit() and 1 <= int(ref) <= len(self.inputs):
            return int(ref)
        for i, t in enumerate(self.inputs, 1):
            if t.lower() == ref.lower():
                return i
        return None

    def tally(self):
        live = {self.active, *self.overlays.values()}
        return "".join("1" if i in live else "2" if i == self.preview else "0" for i in range(1, len(self.inputs) + 1))

    def xml(self):
        ins = "".join(f'<input key="k{i}" number="{i}" type="Capture" title="{t}">{t}</input>'
                      for i, t in enumerate(self.inputs, 1))
        ovl = "".join(f'<overlay number="{n}">{v}</overlay>' for n, v in sorted(self.overlays.items()))
        return (f"<vmix><version>27.0.0.0</version><inputs>{ins}</inputs><overlays>{ovl}</overlays>"
                f"<preview>{self.preview}</preview><active>{self.active}</active></vmix>").encode()

    def operator_cut(self, n):
        """Simulate the vMix operator cutting to input n."""
        with self.lock:
            self.active = int(n)
        self._tally_changed()

    def _tally_changed(self):
        line = f"TALLY OK {self.tally()}\r\n".encode()
        for c in list(self.subs):
            try:
                c.sendall(line)
            except OSError:
                self.subs.discard(c)

    def function(self, name, query):
        q = {k: v[0] for k, v in parse_qs(query).items()}
        self.calls.append((name, q))
        lname, changed = name.lower(), False
        with self.lock:
            inp = self.resolve(q.get("Input", "")) if "Input" in q else None
            if lname == "cutdirect" and inp:
                self.active, changed = inp, True
            elif lname == "previewinput" and inp:
                self.preview, changed = inp, True
            elif lname == "settext" and inp:
                self.texts[(inp, q.get("SelectedName", ""))] = q.get("Value", "")
            elif lname.startswith("overlayinput") and lname.endswith("in") and inp:
                self.overlays[int(lname[12:-2])] = inp; changed = True
            elif lname.startswith("overlayinput") and lname.endswith("out"):
                self.overlays.pop(int(lname[12:-3]), None); changed = True
            elif "Input" in q and not inp:
                return f"FUNCTION ER Input not found\r\n"
        if changed:
            self._tally_changed()
        return f"FUNCTION OK {name}\r\n"

    # ---------------------------------------------------------------- network
    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.clients.append(c)
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _client(self, c):
        buf = b""
        try:
            while True:
                d = c.recv(4096)
                if not d:
                    break
                buf += d
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    cmd = line.decode()
                    if cmd == "SUBSCRIBE TALLY":
                        self.subs.add(c); c.sendall(b"SUBSCRIBE OK TALLY\r\n")
                    elif cmd == "TALLY":
                        c.sendall(f"TALLY OK {self.tally()}\r\n".encode())
                    elif cmd == "XML":
                        x = self.xml(); c.sendall(f"XML {len(x)}\r\n".encode() + x)
                    elif cmd.startswith("FUNCTION "):
                        parts = cmd.split(" ", 2)
                        c.sendall(self.function(parts[1], parts[2] if len(parts) > 2 else "").encode())
                    else:
                        c.sendall(b"ER unknown command\r\n")
        except OSError:
            pass
        finally:
            self.subs.discard(c)

    def close(self):
        try:
            self.srv.shutdown(socket.SHUT_RDWR)        # wakes the thread blocked in accept()
        except OSError:
            pass
        self.srv.close()
        for c in self.clients:
            try:
                c.shutdown(socket.SHUT_RDWR); c.close()
            except OSError:
                pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8099); a = ap.parse_args()
    v = FakeVmix(a.port)
    print(f"fake vMix on 127.0.0.1:{v.port}  (cut N | preview N | show | quit)", flush=True)
    for line in sys.stdin:
        p = line.split()
        if p[:1] == ["cut"] and len(p) == 2: v.operator_cut(p[1])
        elif p[:1] == ["preview"] and len(p) == 2: v.function("PreviewInput", f"Input={p[1]}")
        elif p[:1] == ["show"]: print(f"  active {v.active} preview {v.preview} overlays {v.overlays} texts {v.texts}")
        elif p[:1] == ["quit"]: break
