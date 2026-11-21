#!/usr/bin/env python3
"""Regenerate values.json (code -> value tables for the audio commands) from mcs8m_dump.py."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcs8m_dump as d

out = {
    "_note": "Index = code sent/received. Fader codes 0-255; others 0-30. dB unless noted.",
    "channel_fader_db": d.FADER_DB,          # also MONITOR LEVEL
    "master_fader_db": d.MASTER_DB,
    "trim_and_eq_gain_db": d.GAIN_DB,
    "eq_low_hz": d.EQ_LOW_HZ,
    "eq_mid_hz": d.EQ_MID_HZ,
    "eq_high_hz": d.EQ_HIGH_HZ,
    "pan": d.PAN,
    "master_channel": {str(k): v for k, v in d.MASTER_CH.items()},
    "monitor_select": {str(k): v for k, v in d.MON_SEL.items()},
    "next_transition": {str(k): v for k, v in d.NEXT_TRANS.items()},
    "transition_type": {f"0x{k:02X}": v for k, v in d.TRANS_TYPE.items()},
    "transition_func": {f"0x{k:02X}": v for k, v in d.TRANS_FUNC.items()},
}
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "values.json")
with open(path, "w") as f:
    json.dump(out, f, indent=1)
    f.write("\n")
print("wrote", path)
