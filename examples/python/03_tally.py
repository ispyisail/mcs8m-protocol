#!/usr/bin/env python3
"""Example 3 - a simple tally: prints whenever the on-air source changes.
Uses the switcher's own push messages for instant updates, plus a slow poll as a
safety net.  Read-only.   python3 03_tally.py [/dev/ttyUSB0]      (Ctrl+C to stop)"""
import sys, time
from mcs8m import MCS8M, ME, BUS_A

POLL_EVERY = 10          # seconds; pushes normally arrive long before this

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
with MCS8M(port) as sw:
    on_air, last_poll = sw.program(), time.monotonic()
    print(f"ON AIR: {on_air}")
    while True:
        for ev in sw.listen(0.5):
            if ev.kind == "video" and ev.eff == ME and ev.cmd == BUS_A:
                if ev.data[1] != on_air:
                    on_air = ev.data[1]
                    print(f"ON AIR: {on_air}")
            elif ev.kind == "video" and ev.eff == 0x20 and ev.cmd == 0x37:
                print("  transition running..." if ev.data[-1] else "  transition done")
        if time.monotonic() - last_poll > POLL_EVERY:
            last_poll = time.monotonic()
            now = sw.program()
            if now != on_air:
                on_air = now
                print(f"ON AIR: {on_air}  (found by poll)")
