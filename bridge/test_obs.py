#!/usr/bin/env python3
"""
test_obs.py - end-to-end tests for the OBS connector (simulator + fake_obs.py),
plus OBS and vMix together.   python3 bridge/test_obs.py
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcs8m_bridge as B                                       # noqa: E402
from fake_obs import FakeObs                                   # noqa: E402
from fake_vmix import FakeVmix                                 # noqa: E402
from test_bridge import FAILS, Client, Sim, check, wait_for    # noqa: E402

SPEED = [] if os.environ.get("REAL_SPEED") else ["--fast"]


def sets(o):
    return [rd.get("sceneName") for rt, rd in o.requests if rt == "SetCurrentProgramScene"]


def main():
    B.log = lambda *a: None
    obs = FakeObs(port=0, password="pw123", studio=True)
    sim = Sim(*SPEED)
    cfg = {"serial_port": sim.port, "http": {"host": "127.0.0.1", "port": 0}, "poll_seconds": 3,
           "inputs": {"1": "Camera 1", "2": "Camera 2", "3": "Camera 3"},
           "obs": {"enabled": True, "host": "127.0.0.1", "port": obs.port, "password": "pw123", "mode": "switcher_leads",
                   "scenes": {"1": "Camera 1", "2": "Camera 2", "3": "Camera 3", "5": "Slides", "4": "Missing Scene"},
                   "preview_follows_preset": True, "control": {"transition": "cut"}}}
    bridge, srv = B.start(cfg)
    c = Client(f"http://127.0.0.1:{srv.server_address[1]}")
    try:
        print("connection (with password)")
        check("connected", wait_for(lambda: c.state()["apps"].get("obs", {}).get("connected")), c.state()["apps"])
        check("switcher state read", wait_for(lambda: c.state()["program"] == 2))
        check("start-up sync: OBS brought in line with the Sony (Camera 2)",
              wait_for(lambda: obs.program == "Camera 2" and c.state()["apps"]["obs"]["program"] == "Camera 2"), obs.program)
        time.sleep(0.8)
        check("...without cutting the Sony", c.state()["program"] == 2)

        print("switcher leads")
        obs.requests.clear()
        sim.panel("p 3")
        check("OBS switches to 'Camera 3'", wait_for(lambda: obs.program == "Camera 3"), obs.program)
        time.sleep(0.8)
        check("echo ignored: Sony not cut back", c.state()["program"] == 3 and sets(obs) == ["Camera 3"], sets(obs))
        sim.panel("p 6")
        time.sleep(0.8)
        check("unmapped Sony input leaves OBS alone", obs.program == "Camera 3")
        c.events.clear()
        sim.panel("p 4")
        check("missing OBS scene reported as an error", wait_for(lambda: any(e.get("type") == "app_error" and "Missing Scene" in e["error"]
                                                                              for e in c.events)), [e for e in c.events if e.get("type") != "change"])
        check("studio mode: Sony preset follows to OBS preview",
              c.post("/api/preset/1")[0] == 202 and wait_for(lambda: obs.preview == "Camera 1"), obs.preview)
        obs.switch("Slides")
        time.sleep(0.8)
        check("in switcher_leads an OBS change does NOT move the Sony", c.state()["program"] == 4)

        print("OBS leads (hot cut)")
        check("mode app_leads", c.post("/api/mode/obs/app_leads")[0] == 202)
        obs.requests.clear(); c.events.clear()
        obs.switch("Camera 2")
        check("Sony cuts to 2, origin obs", wait_for(lambda: any(e["value"] == 2 and e["origin"] == "obs" for e in c.changes("program"))),
              c.changes("program"))
        time.sleep(0.8)
        check("OBS not set back (no ping-pong)", obs.program == "Camera 2" and not sets(obs), sets(obs))
        obs.switch("Starting Soon")
        time.sleep(0.8)
        check("unmapped OBS scene leaves the Sony alone", c.state()["program"] == 2)

        print("both")
        check("mode both", c.post("/api/mode/obs/both")[0] == 202)
        obs.requests.clear()
        sim.panel("p 1")
        check("Sony -> OBS", wait_for(lambda: obs.program == "Camera 1"))
        obs.switch("Slides")
        check("OBS -> Sony", wait_for(lambda: c.state()["program"] == 5))
        time.sleep(1.2)
        check("nothing bounced", obs.program == "Slides" and sets(obs) == ["Camera 1"] and c.state()["program"] == 5, (sets(obs), c.state()["program"]))

        print("reconnect")
        port = obs.port
        obs.close()
        check("OBS gone -> disconnected", wait_for(lambda: not c.state()["apps"]["obs"]["connected"], 6))
        obs = FakeObs(port=port, password="pw123", studio=True)
        obs.switch("Starting Soon")                              # OBS restarted on another scene
        check("OBS back -> reconnected", wait_for(lambda: c.state()["apps"]["obs"]["connected"], 12))
        check("...and re-synced to the Sony", wait_for(lambda: obs.program == "Slides"), obs.program)
    finally:
        bridge.stop.set(); sim.close(); obs.close()

    print("wrong password")
    obs = FakeObs(port=0, password="right")
    sim = Sim("--fast")
    bridge, srv = B.start({"serial_port": sim.port, "http": {"host": "127.0.0.1", "port": 0},
                           "obs": {"enabled": True, "host": "127.0.0.1", "port": obs.port, "password": "wrong"}})
    try:
        time.sleep(2)
        check("wrong password -> not connected", not bridge.apps["obs"]["connected"])
    finally:
        bridge.stop.set(); sim.close(); obs.close()

    print("OBS and vMix together, both in 'both' mode")
    obs, vm, sim = FakeObs(port=0), FakeVmix(port=0), Sim(*SPEED)
    bridge, srv = B.start({
        "serial_port": sim.port, "http": {"host": "127.0.0.1", "port": 0}, "poll_seconds": 3,
        "inputs": {"1": "Camera 1", "2": "Camera 2", "3": "Camera 3"},
        "obs": {"enabled": True, "host": "127.0.0.1", "port": obs.port, "mode": "both",
                "scenes": {"1": "Camera 1", "2": "Camera 2", "3": "Camera 3"}},
        "vmix": {"enabled": True, "host": "127.0.0.1", "port": vm.port, "mode": "both",
                 "lower_third": {"enabled": True, "input": "Lower Third", "field": "Headline.Text", "seconds": 0},
                 "on_program": {"2": [{"function": "CutDirect", "Input": "6"}]},
                 "control": {"vmix_to_switcher": {"5": 3}}}})
    c = Client(f"http://127.0.0.1:{srv.server_address[1]}")
    try:
        check("both apps connected", wait_for(lambda: all(c.state()["apps"].get(a, {}).get("connected") for a in ("obs", "vmix"))
                                              and c.state()["program"] == 2))
        time.sleep(1.5)
        obs.requests.clear(); vm.calls.clear(); c.events.clear()
        obs.switch("Camera 2")                                   # OBS leads -> Sony 2 -> vMix action CutDirect 6
        check("OBS -> Sony", wait_for(lambda: c.state()["program"] == 2 or any(e["value"] == 2 for e in c.changes("program"))))
        obs.switch("Camera 1")
        check("OBS -> Sony again", wait_for(lambda: c.state()["program"] == 1))
        check("... vMix shows the name", wait_for(lambda: any(q.get("Value") == "Camera 1" for n, q in vm.calls if n == "SetText")))
        vm.operator_cut(5)                                       # vMix leads -> Sony 3 -> OBS 'Camera 3'
        check("vMix -> Sony -> OBS", wait_for(lambda: c.state()["program"] == 3 and obs.program == "Camera 3"),
              (c.state()["program"], obs.program))
        time.sleep(1.5)
        n_events = len(c.changes("program"))
        time.sleep(2.0)
        check("everything settles (no more program changes)", len(c.changes("program")) == n_events and
              c.state()["program"] == 3 and obs.program == "Camera 3" and vm.active == 5,
              (c.changes("program")[-3:], obs.program, vm.active))
    finally:
        bridge.stop.set(); sim.close(); obs.close(); vm.close()
    print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
