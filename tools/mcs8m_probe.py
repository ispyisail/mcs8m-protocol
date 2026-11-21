#!/usr/bin/env python3
"""
mcs8m_probe.py - search a Sony MCS-8M for undocumented READ commands.

Sends every possible *read-type* command and records anything the switcher
answers. It NEVER sends write-type codes (same guard as mcs8m_dump.py):
  video: CMD 0x00-0x7F only (0x80+ = write)
  audio: CMD with status bit 0x80 set only (bit clear = write)

Extra safety, because undocumented commands carry no guarantees:
  * Skips read codes paired with ACTION commands (auto transition, snapshot
    store/recall) unless explicitly enabled.
  * The switcher announces state changes by itself. If any frame arrives that is
    NOT a reply to a recent probe, the run STOPS immediately and names the probe
    that preceded it. (So: nobody may touch the panel while it runs.)
  * Progress is saved after every probe; re-running resumes where it stopped.

Usage (nothing else may hold the serial port, e.g. stop Node-RED first):
  (stop Node-RED or any other program holding the port)
  python3 mcs8m_probe.py --quick       # ~15 min: every read CMD on known EFFs + audio
  python3 mcs8m_probe.py               # full: + arg variations, other EFFs and
                                       #   sub-address follow-ups, ~1.5-2 h (resumable)
Options: --quick (known EFFs only, no arg variations), --dry-run, --fresh
"""
import argparse, json, os, sys, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcs8m_dump as M

# ------------------------------------------------------------ known commands
# (from the manual + mcs8m_dump.py) so new findings can be flagged
KNOWN_VIDEO = {0x00, 0x01, 0x07, 0x08, 0x10, 0x11, 0x18, 0x1B, 0x1C, 0x33}
KNOWN_AUDIO = {0x10, 0x12, 0x13, 0x14, 0x17, 0x18, 0x19, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F,
               0x20, 0x22, 0x23, 0x33, 0x40, 0x41, 0x43}
# read-side codes whose WRITE twin is an action. Skipped unless --include-risky.
RISKY_VIDEO = {
    (None, 0x16): "AUTO TRANSITION START is 0x96 (write-only per manual)",
    (0x21, 0x00): "SNAPSHOT STORE is 0x80 on E-File",
    (0x21, 0x10): "SNAPSHOT RECALL is 0x90 on E-File",
}

def risky(eff, cmd):
    return RISKY_VIDEO.get((eff, cmd)) or RISKY_VIDEO.get((None, cmd))

# ------------------------------------------------------------ probe plan
def build_plan(quick, include_risky):
    """yields dicts: id, kind, eff, cmd, args, frame"""
    plan = []
    def v(eff, cmd, args, phase):
        if not include_risky and risky(eff, cmd):
            return
        plan.append({"id": f"v:{eff:02X}:{cmd:02X}:{''.join(f'{a:02X}' for a in args) or '-'}",
                     "phase": phase, "kind": "video", "eff": eff, "cmd": cmd, "args": list(args),
                     "frame": M.video_read(eff, cmd, *args).hex(" ")})
    def a(cmd, data, phase):
        plan.append({"id": f"a:{cmd:02X}:{''.join(f'{x:02X}' for x in data)}", "phase": phase,
                     "kind": "audio", "cmd": cmd, "data": list(data),
                     "frame": M.audio_status(cmd, data).hex(" ")})
    effs = [0x00, 0x02, 0x03, 0x20, 0x21]
    # phase 1: every read CMD on each known EFF, no args and with one 00 arg
    for eff in effs:
        for cmd in range(0x80):
            v(eff, cmd, [], "1-video-cmds")
            if not quick:
                v(eff, cmd, [0x00], "1-video-cmds")
    # phase 2: every audio status CMD, channel 0
    for cmd in range(0x80):
        a(cmd, [0x00, 0x00], "2-audio-cmds")
    if not quick:
        # phase 3: discover other EFF (effect/bank) numbers with a few common CMDs
        for eff in range(0x40):
            if eff in effs or eff == 0x01:
                continue
            for cmd in (0x00, 0x01, 0x10, 0x11, 0x18):
                v(eff, cmd, [], "3-video-effs")
    return plan

def follow_ups(results, done_ids, include_risky):
    """phase 4: for every NEW command that answered, scan its first argument
    (sub-address / channel) 0x00-0x1F to map what it contains."""
    extra = []
    seen = set()
    for r in results.values():
        if r.get("status") != "reply":
            continue
        if r["kind"] == "video" and (r["eff"], r["cmd"]) not in seen:
            seen.add((r["eff"], r["cmd"]))
            for sub in range(0x20):
                for args in ([sub], [sub, 0x00]):
                    pid = f"v:{r['eff']:02X}:{r['cmd']:02X}:{''.join(f'{x:02X}' for x in args)}"
                    if pid not in done_ids and (include_risky or not risky(r["eff"], r["cmd"])):
                        extra.append({"id": pid, "phase": "4-follow-up", "kind": "video", "eff": r["eff"],
                                      "cmd": r["cmd"], "args": args,
                                      "frame": M.video_read(r["eff"], r["cmd"], *args).hex(" ")})
        elif r["kind"] == "audio" and ("a", r["cmd"]) not in seen and r["cmd"] not in KNOWN_AUDIO:
            seen.add(("a", r["cmd"]))
            for ch in range(1, 0x10):
                pid = f"a:{r['cmd']:02X}:{ch:02X}00"
                if pid not in done_ids:
                    extra.append({"id": pid, "phase": "4-follow-up", "kind": "audio", "cmd": r["cmd"],
                                  "data": [ch, 0], "frame": M.audio_status(r["cmd"], [ch, 0]).hex(" ")})
    return extra

# ------------------------------------------------------------ matching
def is_reply(p, f):
    pid, _, _, m = M.frame_msg(f)
    if p["kind"] == "video":
        return pid == M.PID_VIDEO and len(m) >= 4 and m[0] == M.PROT and m[2] == p["eff"] and m[3] == (p["cmd"] | 0x80)
    return pid == M.PID_AUDIO and len(m) >= 1 and m[0] == p["cmd"]

class Unsolicited(Exception):
    pass

SEEN_REPLIES = set()   # exact frames already received as replies; a repeat is a duplicate
DUPLICATES = [0]

def run_probe(port, parser, p, recent, timeout):
    """returns (status, reply_hex|None, ms). Raises Unsolicited on unexplained frames."""
    frame = bytes.fromhex(p["frame"])
    M.assert_read_only(frame)
    acks_before = parser.acks
    port.write(frame)
    t0 = time.monotonic()
    deadline = t0 + timeout
    while time.monotonic() < deadline:
        for f in parser.feed(port.read(max(0.0, deadline - time.monotonic()))):
            if is_reply(p, f):
                SEEN_REPLIES.add(f.hex(" "))
                return "reply", f.hex(" "), round((time.monotonic() - t0) * 1000)
            h = f.hex(" ")
            owners = [q for q in recent if is_reply(q, f)]
            if owners:
                # Same command as an earlier probe. Harmless ONLY if that probe had no
                # answer yet (late reply) or this is a byte-identical repeat. A reply
                # with DIFFERENT data means the state changed -> stop.
                if any(q.get("_rx") == h for q in owners):
                    DUPLICATES[0] += 1
                    continue
                pending = [q for q in owners if q.get("_rx") is None]
                if pending:
                    pending[-1]["_rx"] = h; pending[-1]["_late"] = True
                    continue
                raise Unsolicited(h)
            if h in SEEN_REPLIES:             # the unit sometimes repeats a reply ~1.5 s later
                DUPLICATES[0] += 1
                continue
            raise Unsolicited(h)
    if parser.acks > acks_before:
        return "ack-only", None, round((time.monotonic() - t0) * 1000)
    return "silent", None, None

# ------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Search the MCS-8M for undocumented read commands (read-only).")
    ap.add_argument("-p", "--port", default="/dev/ttyUSB0")
    ap.add_argument("-o", "--output", default="mcs8m_probe.json")
    ap.add_argument("--video-timeout", type=float, default=1.4, help="valid video replies take ~1.01 s")
    ap.add_argument("--audio-timeout", type=float, default=0.3, help="valid audio replies take ~11 ms")
    ap.add_argument("--gap", type=float, default=0.03)
    ap.add_argument("--quick", action="store_true", help="known EFFs, no argument variations or follow-ups")
    ap.add_argument("--include-risky", action="store_true", help="also probe read codes paired with action commands")
    ap.add_argument("--fresh", action="store_true", help="ignore saved progress")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    plan = build_plan(a.quick, a.include_risky)
    state = {"results": {}, "stopped": None}
    if os.path.exists(a.output) and not a.fresh:
        state = json.load(open(a.output))
        print(f"resuming: {len(state['results'])} probes already done", file=sys.stderr)
    if a.dry_run:
        for p in plan[:20]:
            print(p["id"], p["frame"])
        print(f"... {len(plan)} probes in phases 1-3 (+ follow-ups for anything new)")
        return 0
    if state.get("stopped"):
        print("previous run STOPPED on an unsolicited frame:\n  " + json.dumps(state["stopped"]) +
              "\nInvestigate before continuing. Use --fresh to start over.", file=sys.stderr)
        return 3

    def save():
        state["updated"] = datetime.now().isoformat(timespec="seconds")
        state["summary"] = summarize(state["results"])
        state["summary"]["duplicate_replies_ignored"] = state.get("dups_prev", 0) + DUPLICATES[0]
        tmp = a.output + ".tmp"
        json.dump(state, open(tmp, "w"), indent=1)
        os.replace(tmp, a.output)

    for r in state["results"].values():      # duplicates of replies from earlier sessions too
        if r.get("rx"):
            SEEN_REPLIES.add(r["rx"])
    port = M.Port(a.port)
    parser = M.FrameParser()
    port.read(0.3); parser.feed(b"")
    recent = []
    queue = [p for p in plan if p["id"] not in state["results"]]
    followups_added = a.quick
    i = 0
    try:
        while True:
            if not queue:
                if followups_added:
                    break
                queue = follow_ups(state["results"], set(state["results"]), a.include_risky)
                followups_added = True
                print(f"phase 4: {len(queue)} follow-up probes", file=sys.stderr)
                continue
            p = queue.pop(0); i += 1
            try:
                status, rx, ms = run_probe(port, parser, p,
                                           recent, a.video_timeout if p["kind"] == "video" else a.audio_timeout)
            except Unsolicited as e:
                state["stopped"] = {"after_probe": p["id"], "tx": p["frame"], "unsolicited_frame": str(e),
                                    "meaning": M.describe(bytes.fromhex(str(e))),
                                    "previous_probes": [q["id"] for q in recent]}
                save()
                print("\n*** STOPPED: the switcher reported a change it was not asked about.\n"
                      f"    last probe: {p['id']}  ({p['frame']})\n"
                      f"    frame:      {e}\n    meaning:    {state['stopped']['meaning']}\n"
                      "    Check the switcher output. Someone touching the panel also causes this.",
                      file=sys.stderr)
                return 3
            rec = {k: p[k] for k in ("phase", "kind", "eff", "cmd", "args", "data", "frame") if k in p}
            rec.update(status=status, rx=rx, ms=ms,
                       new=(status == "reply" and ((p["kind"] == "video" and p["cmd"] not in KNOWN_VIDEO) or
                                                   (p["kind"] == "audio" and p["cmd"] not in KNOWN_AUDIO))))
            state["results"][p["id"]] = rec
            p["_rx"] = rx
            for q in recent:                    # late replies that arrived during this probe
                if q.pop("_late", False) and q["id"] in state["results"]:
                    state["results"][q["id"]].update(status="reply", rx=q["_rx"], ms=None, late=True)
            recent = (recent + [p])[-8:]
            mark = "NEW!" if rec["new"] else ""
            print(f"[{i:4d} | {len(queue):4d} left] {p['id']:16s} {status:8s} {mark} {rx or ''}", file=sys.stderr)
            if i % 10 == 0:
                save()
            time.sleep(a.gap)
    except KeyboardInterrupt:
        print("\ninterrupted - progress saved, re-run to resume", file=sys.stderr)
    finally:
        save()
        port.close()
    s = state["summary"]
    print(json.dumps({"output": a.output, **{k: s[k] for k in ("probes", "replies", "new_commands")}}))
    return 0

def summarize(results):
    replies = [r for r in results.values() if r["status"] == "reply"]
    new = sorted({(r["kind"], r.get("eff"), r["cmd"]) for r in replies if r.get("new")}, key=str)
    return {"probes": len(results), "replies": len(replies),
            "silent": sum(r["status"] == "silent" for r in results.values()),
            "ack_only": sum(r["status"] == "ack-only" for r in results.values()),
            "new_commands": [f"{k} EFF 0x{e:02X} CMD 0x{c:02X}" if e is not None else f"{k} CMD 0x{c:02X}"
                             for k, e, c in new]}

if __name__ == "__main__":
    sys.exit(main())
