#!/usr/bin/env python3
"""
test_bridge.py - end-to-end tests for the bridge, using tools/simulator.py
(Linux/macOS).   python3 bridge/test_bridge.py      REAL_SPEED=1 for real ~1 s video timing
"""
import json, os, subprocess, sys, threading, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcs8m_bridge as B                                        # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok    " if cond else "  FAIL  ") + name + ("" if cond else f"   {detail}"))
    if not cond:
        FAILS.append(name)


def wait_for(pred, timeout=8.0 if not os.environ.get("REAL_SPEED") else 15.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class Sim:
    def __init__(self, *flags):
        self.p = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "..", "tools", "simulator.py"), *flags],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.port = self.p.stdout.readline().split()[2]

    def panel(self, line):
        self.p.stdin.write(line + "\n"); self.p.stdin.flush()

    def close(self):
        self.p.kill()


class Client:
    def __init__(self, base, token=""):
        self.base, self.token, self.events = base, token, []
        threading.Thread(target=self._sse, daemon=True).start()

    def _sse(self):
        with urllib.request.urlopen(self.base + "/api/events") as r:
            for line in r:
                line = line.decode().strip()
                if line.startswith("data: "):
                    self.events.append(json.loads(line[6:]))

    def post(self, path, token=None):
        req = urllib.request.Request(self.base + path, method="POST", data=b"")
        t = self.token if token is None else token
        if t:
            req.add_header("Authorization", "Bearer " + t)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def state(self):
        with urllib.request.urlopen(self.base + "/api/state") as r:
            return json.loads(r.read())

    def changes(self, key):
        return [e for e in self.events if e.get("type") == "change" and e.get("key") == key]


def main():
    B.log = lambda *a: None                                     # keep the test output readable
    sim = Sim(*([] if os.environ.get("REAL_SPEED") else ["--fast"]), "--duplicates")
    cfg = {"serial_port": sim.port, "http": {"host": "127.0.0.1", "port": 0, "token": "s3cret"},
           "poll_seconds": 3, "inputs": {"1": "Camera 1", "8": "vMix return"},
           "loop_protection": {"switcher_input_from_vmix": 8, "vmix_input_from_switcher": "1"}}
    bridge, srv = B.start(cfg)
    c = Client(f"http://127.0.0.1:{srv.server_address[1]}", token="s3cret")
    try:
        print("state from the first poll")
        check("connected", wait_for(lambda: c.state()["connected"] and c.state()["aux2"] is not None))
        s = c.state()
        check("program/preset/aux read", (s["program"], s["preset"], s["aux1"], s["aux2"]) == (2, 7, 7, 7), s)
        check("mutes read (CH1-5 muted, CH6 live)",
              wait_for(lambda: c.state()["muted"] == {"1": True, "2": True, "3": True, "4": True, "5": True, "6": False}),
              c.state()["muted"])

        print("commands over HTTP")
        check("aux 1 -> 3 accepted", c.post("/api/aux/1/3")[0] == 202)
        check("aux1 becomes 3, origin api", wait_for(lambda: any(e["value"] == 3 and e["origin"] == "api"
                                                              for e in c.changes("aux1"))), c.changes("aux1"))
        check("preset 4", c.post("/api/preset/4")[0] == 202 and wait_for(lambda: c.state()["preset"] == 4))
        c.events.clear()
        check("auto 15", c.post("/api/auto/15")[0] == 202)
        check("transition true then false",
              wait_for(lambda: [e["value"] for e in c.changes("transition")] == [True, False]),
              c.changes("transition"))
        check("after AUTO TRANS program 4 / preset 2", wait_for(lambda: (c.state()["program"], c.state()["preset"]) == (4, 2)),
              (c.state()["program"], c.state()["preset"]))
        check("program change attributed to api", any(e["origin"] == "api" and e["value"] == 4 for e in c.changes("program")),
              c.changes("program"))
        check("mute CH1 off", c.post("/api/mute/1/off")[0] == 202 and wait_for(lambda: c.state()["muted"]["1"] is False))
        check("hot cut program 6", c.post("/api/program/6")[0] == 202 and wait_for(lambda: c.state()["program"] == 6))

        print("changes made on the switcher's panel")
        c.events.clear()
        sim.panel("p 1")
        check("panel cut arrives as an event, origin switcher",
              wait_for(lambda: any(e["value"] == 1 and e["origin"] == "switcher" for e in c.changes("program"))),
              c.changes("program"))
        check("event carries the input name", any(e.get("name") == "Camera 1" for e in c.changes("program")))

        print("errors and security")
        check("no token -> 401", c.post("/api/aux/1/2", token="x")[0] == 401)
        check("input 15 -> 409", c.post("/api/program/15")[0] == 409)
        check("unknown command -> 404", c.post("/api/fly")[0] == 404)
        check("mode for an app that isn't enabled -> 409", c.post("/api/mode/vmix/both")[0] == 409)

        print("video-loop guard (vMix return = input 8, vMix input 1 = the switcher)")
        bridge.register_app("vmix", "switcher_leads")
        bridge.update_app("vmix", program="1")                    # vMix is showing the switcher's output
        code, body = c.post("/api/program/8")
        check("cut to the vMix return is refused", code == 409 and "loop" in body["error"], (code, body))
        check("preset 8 is allowed (not on air)", c.post("/api/preset/8")[0] == 202 and wait_for(lambda: c.state()["preset"] == 8))
        check("AUTO TRANS that would bring 8 to air is refused", c.post("/api/auto")[0] == 409)
        bridge.update_app("vmix", program="3")                    # vMix moved away from the capture input
        check("now cutting to 8 is allowed", c.post("/api/program/8")[0] == 202 and wait_for(lambda: c.state()["program"] == 8))
        try:
            bridge.guard_vmix("1"); refused = False
        except B.Refused:
            refused = True
        check("vMix -> capture input refused while 8 is on air", refused)
        c.events.clear()
        bridge.update_app("vmix", program="1")                    # someone does it by hand in vMix anyway
        check("a loop made by hand raises a warning", wait_for(lambda: bool(c.state()["loop_warning"])), c.state())
        check("... and a loop event", any(e.get("type") == "loop" and e.get("warning") for e in c.events))
        sim.panel("p 2")                                            # operator cuts away on the Sony
        check("warning clears when the loop is broken", wait_for(lambda: c.state()["loop_warning"] is None))

        print("modes")
        check("set vmix mode both", c.post("/api/mode/vmix/both")[0] == 202 and c.state()["apps"]["vmix"]["mode"] == "both")
        check("bad mode -> 409", c.post("/api/mode/vmix/sideways")[0] == 409)
    finally:
        bridge.stop.set()
        sim.close()
    print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
