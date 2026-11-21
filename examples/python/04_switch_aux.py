#!/usr/bin/env python3
"""Example 4 - WRITE: route a source to an Aux output, then read it back.
This CHANGES what the Aux output shows. It refuses to run without --yes.

    python3 04_switch_aux.py --aux 1 --button 3 --yes [--port /dev/ttyUSB0]"""
import argparse
from mcs8m import MCS8M

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="/dev/ttyUSB0")
ap.add_argument("--aux", type=int, choices=(1, 2), required=True)
ap.add_argument("--button", type=int, choices=range(1, 15), metavar="1-14", required=True)
ap.add_argument("--yes", action="store_true", help="I know this changes the switcher output")
a = ap.parse_args()
if not a.yes:
    ap.error("this changes a live output - add --yes to confirm")

with MCS8M(a.port, allow_writes=True) as sw:
    print(f"Aux {a.aux}: {sw.aux(a.aux)} -> {a.button}")
    sw.set_aux(a.aux, a.button)
    print(f"Aux {a.aux} now reads: {sw.aux(a.aux)}")
