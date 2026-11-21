#!/usr/bin/env python3
"""
test_vmix.py - end-to-end tests for the vMix connector, using tools/simulator.py
as the switcher and fake_vmix.py as vMix.   python3 bridge/test_vmix.py
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcs8m_bridge as B                                       # noqa: E402
from fake_vmix import FakeVmix                                 # noqa: E402
from test_bridge import FAILS, Client, Sim, check, wait_for    # noqa: E402


def calls(v, name):
    return [q for n, q in v.calls if n.lower() == name.lower()]


def main():
    B.log = lambda *a: None
    vm = FakeVmix(port=0)                  # vMix inputs: 1 MCS-8M capture, 2 Camera PiP, 3 Lower Third, ... 7 Graphics
    sim = Sim(*([] if os.environ.get("REAL_SPEED") else ["--fast"]))
    cfg = {"serial_port": sim.port, "http": {"host": "127.0.0.1", "port": 0}, "poll_seconds": 3,
           "inputs": {"1": "Camera 1", "2": "Camera 2", "3": "Camera 3", "4": "Camera 4", "8": "vMix return"},
           "loop_protection": {"switcher_input_from_vmix": 8, "vmix_input_from_switcher": "1"},
           "vmix": {"enabled": True, "host": "127.0.0.1", "port": vm.port, "mode": "switcher_leads",
                    "lower_third": {"enabled": True, "input": "Lower Third", "field": "Headline.Text",
                                    "overlay": 1, "seconds": 1.0, "for_inputs": []},
                    "on_program": {"4": [{"function": "CutDirect", "Input": "2"}],
                                   "8": [{"function": "CutDirect", "Input": "1"}]},
                    "control": {"vmix_to_switcher": {"5": 6, "6": 3, "7": 8}, "transition": "cut"}}}
    bridge, srv = B.start(cfg)
    c = Client(f"http://127.0.0.1:{srv.server_address[1]}")
    try:
        print("connection")
        check("vMix connected and its program read", wait_for(lambda: c.state()["apps"].get("vmix", {}).get("connected")
                                                              and c.state()["apps"]["vmix"]["program"] == "1"), c.state()["apps"])
        check("switcher state read", wait_for(lambda: c.state()["program"] == 2))

        print("switcher leads: lower third with the camera name")
        vm.calls.clear()
        sim.panel("p 3")
        check("SetText 'Camera 3' on the Lower Third title",
              wait_for(lambda: any(q.get("Value") == "Camera 3" and q.get("Input") == "Lower Third"
                                   and q.get("SelectedName") == "Headline.Text" for q in calls(vm, "SetText"))), vm.calls)
        check("overlay 1 shown with the title", wait_for(lambda: vm.overlays.get(1) == 3), vm.overlays)
        check("overlay hidden again after 1 s", wait_for(lambda: 1 not in vm.overlays, 4), vm.overlays)
        check("vMix program untouched (no action for input 3)", vm.active == 1)

        print("switcher leads: extra action (Sony input 4 -> vMix CutDirect 2), echo ignored")
        vm.calls.clear(); c.events.clear()
        sim.panel("p 4")
        check("vMix cut to input 2", wait_for(lambda: vm.active == 2), vm.active)
        time.sleep(1.0)
        check("bridge saw vMix on 2", c.state()["apps"]["vmix"]["program"] == "2")
        check("Sony not touched back (no ping-pong)", c.state()["program"] == 4 and
              not any(e["origin"] == "vmix" for e in c.changes("program")), c.changes("program"))
        vm.operator_cut(5)
        time.sleep(1.0)
        check("in switcher_leads, a vMix cut does NOT move the Sony", c.state()["program"] == 4)

        print("vMix leads (hot cut)")
        check("set mode app_leads", c.post("/api/mode/vmix/app_leads")[0] == 202)
        c.events.clear(); vm.calls.clear()
        vm.operator_cut(6)                                       # vMix 6 -> Sony 3
        check("Sony hot-cuts to 3, origin vmix", wait_for(lambda: any(e["value"] == 3 and e["origin"] == "vmix"
                                                                       for e in c.changes("program"))), c.changes("program"))
        check("lower third still shows the name", wait_for(lambda: any(q.get("Value") == "Camera 3" for q in calls(vm, "SetText"))))
        time.sleep(0.8)
        check("vMix not cut back (no ping-pong)", vm.active == 6 and not calls(vm, "CutDirect"), (vm.active, vm.calls))
        vm.operator_cut(4)                                       # not mapped
        time.sleep(1.0)
        check("unmapped vMix input leaves the Sony alone", c.state()["program"] == 3)
        sim.panel("p 4")                                         # Sony panel in app_leads: lower third only
        check("Sony panel cut in app_leads: no vMix action", wait_for(lambda: c.state()["program"] == 4) and
              (time.sleep(0.8) or vm.active == 4) and not calls(vm, "CutDirect"), vm.calls)

        print("both: either side leads, nothing bounces")
        check("set mode both", c.post("/api/mode/vmix/both")[0] == 202)
        vm.operator_cut(3); time.sleep(0.5)
        vm.calls.clear(); c.events.clear()
        sim.panel("p 4")                                         # Sony 4 -> action vMix CutDirect 2
        sim.panel("p 1"); sim.panel("p 4")
        check("Sony cut drives vMix", wait_for(lambda: vm.active == 2), vm.active)
        time.sleep(1.2)
        check("...and vMix's change is not sent back", c.state()["program"] == 4 and
              not any(e["origin"] == "vmix" for e in c.changes("program")), c.changes("program"))
        vm.calls.clear(); c.events.clear()
        vm.operator_cut(5)                                       # vMix 5 -> Sony 6
        check("vMix cut drives the Sony", wait_for(lambda: c.state()["program"] == 6), c.state()["program"])
        time.sleep(1.2)
        check("...and the Sony's change is not sent back", vm.active == 5 and not calls(vm, "CutDirect"), vm.calls)

        print("video-loop guard with vMix's real program")
        vm.operator_cut(7)                                       # vMix 7 -> Sony 8 (vMix not on the capture input: allowed)
        check("Sony may take the vMix return while vMix isn't showing the Sony",
              wait_for(lambda: c.state()["program"] == 8), c.state()["program"])
        check("no lower third for the vMix return", not any(q.get("Value") == "vMix return" for q in calls(vm, "SetText")))
        code, body = c.post("/api/mode/vmix/switcher_leads")
        vm.calls.clear(); c.events.clear()
        sim.panel("p 1"); time.sleep(0.4); sim.panel("p 8")     # action for 8 wants vMix -> 1 (the capture): a loop
        check("action that would close the loop is refused",
              wait_for(lambda: any(e.get("type") == "refused" for e in c.events)), [e for e in c.events if e.get("type") != "change"])
        check("...vMix stays off the capture input", vm.active != 1, vm.active)
        vm.operator_cut(1)                                       # the vMix operator does it by hand
        check("a loop made by hand raises the warning", wait_for(lambda: bool(c.state()["loop_warning"])), c.state())
        code, body = c.post("/api/program/8")
        check("bridge refuses to add to it", code == 409)
        sim.panel("p 2")
        check("warning clears when the Sony cuts away", wait_for(lambda: c.state()["loop_warning"] is None))

        print("reconnect")
        port = vm.port
        vm.close()
        check("vMix gone -> disconnected", wait_for(lambda: not c.state()["apps"]["vmix"]["connected"], 6))
        vm = FakeVmix(port=port)
        check("vMix back -> reconnected", wait_for(lambda: c.state()["apps"]["vmix"]["connected"], 12))
    finally:
        bridge.stop.set(); sim.close(); vm.close()
    print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
