#!/usr/bin/env python3
"""
check_commands.py - validate commands.json.

1. Every enum/table it references exists, and every confidence label is known.
2. Using ONLY commands.json (framing rules + command specs), build every documented
   read request and compare with what the tested dump tool (mcs8m_dump.py) sends.
3. Every example frame has a correct BC, checksum and ETX, and its command is in the spec.
4. Write frames built from commands.json match tools/companion_commands.py.

    python3 tools/check_commands.py        (exit code 1 on any problem)
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
spec = json.load(open(os.path.join(ROOT, "commands.json"), encoding="utf-8"))
problems = []
x = lambda s: int(s, 16)


# ---- frame builder driven by commands.json
def build(family, msg):
    fam = spec["families"][family]
    payload = [x(fam["pid"]), x(spec["framing"]["layout"][4]["value"]), x(fam["tid"]), 0, 0, *msg]
    bc_field = spec["framing"]["layout"][2]
    body = list(len(payload).to_bytes(2, bc_field["endian"])) + payload     # follow the spec, then compare
    return bytes([0x01, 0x02, *body, (-sum(body)) & 0xFF, 0x03])


def video_msg(eff, cmd, args):
    return [0x03, 2 + len(args), eff, cmd, *args]


def arg_choices(arg, section):
    """All values a read argument can take (for building every read request)."""
    if arg["type"] == "fixed":
        return [x(arg["value"])]
    if arg["type"] == "enum":
        allowed = arg.get("allowed_by_section", {}).get(section) or arg.get("allowed") \
            or [k for k in spec["enums"][arg["enum"]] if not k.startswith("_")]
        return [x(v) for v in allowed]
    if arg["name"] == "channel":
        return list(range(arg["min"], arg["max"] + 1))
    raise ValueError(f"can't enumerate read arg {arg}")


def combos(lists):
    out = [[]]
    for l in lists:
        out = [o + [v] for o in out for v in l]
    return out


# ---- 1. references
bc = spec["framing"]["layout"][2]
if bc.get("field") != "BC" or bc.get("endian") != "big":
    problems.append(f"framing: BC must be big-endian (verified on hardware), spec says {bc.get('endian')!r}")
labels = set(spec["confidence_levels"])
for c in spec["video_commands"] + spec["audio_commands"]:
    for side, conf in c["confidence"].items():
        if conf is not None and conf.split(" ")[0] not in labels:
            problems.append(f"{c['id']}: unknown confidence {conf!r}")
    args = (c.get("write") or {}).get("args", []) + ((c.get("read") or {}).get("args", [])) + c.get("data", [])
    for a in args:
        if a["type"] == "enum" and a["enum"] not in spec["enums"]:
            problems.append(f"{c['id']}: missing enum {a['enum']}")
        if a["type"] == "table" and a["table"] not in spec["value_tables"]:
            problems.append(f"{c['id']}: missing table {a['table']}")

# ---- 2. every read request, built from the spec
built = set()
for c in spec["video_commands"]:
    if not c["read"]:
        continue
    for sec in c["sections"]:
        for args in combos([arg_choices(a, sec) for a in c["read"]["args"]]):
            built.add(build("video", video_msg(x(sec), x(c["read"]["cmd"]), args)))
for c in spec["audio_commands"]:
    choices = []
    for i, d in enumerate(c["read_data"]):
        if d == "channel":
            choices.append(list(range(6)))
        elif d == "output":
            choices.append([x(k) for k in spec["enums"]["master_channel"]])
        else:
            choices.append([x(d)])
    for data in combos(choices):
        built.add(build("audio", [x(c["read_cmd"]), *data]))

import mcs8m_dump
tested = {frame for _, frame, _ in mcs8m_dump.plan()}
if built != tested:
    for f in sorted(built - tested): problems.append(f"read built from spec but not sent by the dump tool: {f.hex(' ')}")
    for f in sorted(tested - built): problems.append(f"read sent by the dump tool but missing from spec: {f.hex(' ')}")

# ---- 3. examples
video_cmds = {(s, x(c[k]["cmd"])) for c in spec["video_commands"] for s in c["sections"] for k in ("read", "write") if c.get(k)}
audio_cmds = {x(c["cmd"]) for c in spec["audio_commands"]} | {x(c["read_cmd"]) for c in spec["audio_commands"]}
for ex in spec["examples"]:
    for key in ("tx", "rx"):
        if key not in ex:
            continue
        f = bytes.fromhex(ex[key])
        bc = (f[2] << 8) | f[3]
        if len(f) != bc + 6 or f[-1] != 0x03 or sum(f[2:-1]) & 0xFF:
            problems.append(f"example {ex['name']!r} {key}: bad BC/checksum/ETX")
            continue
        msg = f[9:-2]
        known = (f"0x{msg[2]:02X}", msg[3]) in video_cmds if f[4] == 1 else msg[0] in audio_cmds
        if not known:
            problems.append(f"example {ex['name']!r} {key}: command not in the spec")

# ---- 4. writes vs companion_commands.py
import companion_commands as cc
w = {c["id"]: c for c in spec["video_commands"]}
for aux in (1, 2):
    for b in (1, 7, 14):
        f = build("video", video_msg(aux + 1, x(w["xpt_a_bus"]["write"]["cmd"]), [0x00, b]))
        if f != cc.build("aux", aux, b): problems.append(f"aux {aux} button {b}: spec and companion_commands differ")
f = build("video", video_msg(0x21, x(w["snapshot_recall"]["write"]["cmd"]), [3, 0, 0]))
if f != cc.build("recall", 3): problems.append("snapshot recall: spec and companion_commands differ")
f = build("video", video_msg(0x00, x(w["auto_transition"]["write"]["cmd"]), [0x00, 0x00, 0x30]))
if f != cc.build("auto", 30): problems.append("auto transition: spec and companion_commands differ")

n_cmds = len(spec["video_commands"]) + len(spec["audio_commands"])
if problems:
    print("\n".join("PROBLEM: " + p for p in problems)); sys.exit(1)
print(f"commands.json OK: {n_cmds} commands; all {len(built)} read requests built from the spec match "
      f"the tested dump tool exactly; {len(spec['examples'])} examples valid; writes match companion_commands.py")
