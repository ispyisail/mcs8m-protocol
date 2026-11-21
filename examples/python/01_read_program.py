#!/usr/bin/env python3
"""Example 1 - what's on air?  Reads program, preset and both Aux outputs.
Read-only.   python3 01_read_program.py [/dev/ttyUSB0]"""
import sys
from mcs8m import MCS8M

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
with MCS8M(port) as sw:                 # each video read takes ~1 s - that's normal
    print("Program (on air):", sw.program())
    print("Preset  (next):  ", sw.preset())
    print("Aux 1:           ", sw.aux(1))
    print("Aux 2:           ", sw.aux(2))
