#!/usr/bin/env python3
"""Example 2 - audio overview: fader level and mute state of every channel.
Read-only and fast (audio replies take ~11 ms).   python3 02_read_audio.py [/dev/ttyUSB0]"""
import sys
from mcs8m import MCS8M, fader_db

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
with MCS8M(port) as sw:
    faders = sw.faders()
    for ch in range(6):
        name = f"CH{ch + 1}"
        state = "muted" if sw.muted(ch) else "LIVE"
        print(f"{name}: {fader_db(faders[name]):>6} dB  {state}")
    print(f"PGM: {fader_db(faders['PGM']):>6} dB")
